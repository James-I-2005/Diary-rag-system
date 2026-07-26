"""Context Engine / Context Builder：组合会话短期记忆 + 本轮/窗口内召回 → LLM messages。

Prompt 流水线（与会话记忆策略一致）：

    System Prompt
  + Conversation Summary      ← 超出滑动窗口的旧对话压缩
  + Recent Messages           ← 窗口内原文（默认 10 轮）
  + Retrieved Memories        ← 本轮召回 ∪ 窗口内更早轮次曾召回的 chunk
  + Current User Query
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from src.context.conversation import ConversationManager
from src.context.models import (
    BuiltContext,
    ConversationState,
    Message,
    RetrievedMemory,
)
from src.context.tokens import (
    TokenBudget,
    estimate_tokens,
    fit_text,
    resolve_token_budget,
)
from src.engine.candidate import Candidate
from src.store import get_db, load_config

DEFAULT_SYSTEM = """你是用户的陪伴型助手：可以闲聊生活、想法与日常，也可以在有日记材料时帮忙回忆往事。

系统有时会附上检索到的日记原文，以及「命中理由」（匹配到的改写句子）。有材料时自然融入回答；没有材料时照常陪聊，不要拒答。

抽象结论需有原文或命中理由支撑。仅在用户明确追问日记且材料不足时说明查无——仍可继续聊，别硬编。"""


class ContextEngine:
    """
    不负责检索；只消费 Memory Engine 的 Candidate / 已 hydrate 的记忆，
    并结合 ConversationState 构建最终 Prompt。

    会话记忆（本模块职责）：
    - 短期：滑动窗口内原文（session_window_turns）
    - 溢出：压缩为 conversation summary
    - 窗口内曾召回的 chunk：从 retrieval_traces 回灌进 Prompt
    不实现跨会话长期记忆 / 用户画像。
    """

    def __init__(
        self,
        *,
        budget: TokenBudget | None = None,
        conversation: ConversationManager | None = None,
    ):
        self.budget = budget or resolve_token_budget()
        self.conversation = conversation or ConversationManager()

    def _cfg(self) -> dict:
        return load_config().get("context") or {}

    def system_prompt(self) -> str:
        return (
            os.getenv("CONTEXT_SYSTEM_PROMPT", "").strip()
            or (self._cfg().get("system_prompt") or "").strip()
            or DEFAULT_SYSTEM
        )

    def session_window_turns(self) -> int:
        """滑动窗口大小（轮）。优先 session_window_turns，兼容 recent_message_turns。"""
        raw = os.getenv("CONTEXT_SESSION_WINDOW_TURNS", "").strip() or os.getenv(
            "CONTEXT_RECENT_TURNS", ""
        ).strip()
        if raw:
            return max(1, int(raw))
        cfg = self._cfg()
        if "session_window_turns" in cfg:
            return max(1, int(cfg["session_window_turns"]))
        return max(1, int(cfg.get("recent_message_turns", 10)))

    def recent_turns(self) -> int:
        """兼容旧调用名。"""
        return self.session_window_turns()

    def include_prior_retrievals(self) -> bool:
        raw = os.getenv("CONTEXT_INCLUDE_PRIOR_RETRIEVALS", "").strip()
        if raw:
            return raw.lower() not in ("0", "false", "no", "off")
        return bool(self._cfg().get("include_prior_retrievals", True))

    def memory_min_score(self) -> float:
        raw = os.getenv("CONTEXT_MEMORY_MIN_SCORE", "").strip()
        if raw:
            return float(raw)
        return float(self._cfg().get("memory_min_score", 0.0))

    def memory_max_items(self) -> int:
        raw = os.getenv("CONTEXT_MEMORY_MAX_ITEMS", "").strip()
        if raw:
            return int(raw)
        return int(self._cfg().get("memory_max_items", 20))

    def adapt_memories(
        self,
        memories: Iterable[Candidate | RetrievedMemory | dict[str, Any]] | None,
    ) -> list[RetrievedMemory]:
        """统一把 Candidate / hydrate dict / RetrievedMemory 转成 RetrievedMemory。"""
        if not memories:
            return []
        items = list(memories)
        if not items:
            return []

        if all(isinstance(m, RetrievedMemory) for m in items):
            return list(items)  # type: ignore[arg-type]

        if all(isinstance(m, Candidate) for m in items):
            from src.query import hydrate_candidates

            hydrated = hydrate_candidates(list(items))  # type: ignore[arg-type]
            return [RetrievedMemory.from_hydrated(h) for h in hydrated]

        out: list[RetrievedMemory] = []
        for m in items:
            if isinstance(m, RetrievedMemory):
                out.append(m)
            elif isinstance(m, Candidate):
                out.append(RetrievedMemory.from_candidate(m))
            elif isinstance(m, dict):
                out.append(RetrievedMemory.from_hydrated(m))
        return out

    def load_prior_memories(
        self,
        conversation_id: str,
        *,
        max_turns: int | None = None,
    ) -> list[RetrievedMemory]:
        """从近 max_turns 轮 retrieval_traces 还原曾召回的 chunk。"""
        if not conversation_id or not self.include_prior_retrievals():
            return []
        window = max_turns if max_turns is not None else self.session_window_turns()
        traces = self.conversation.list_recent_retrieval_traces(
            conversation_id, limit=window
        )
        if not traces:
            return []

        # chunk_id → 最优候选元信息（保留更高分）
        meta_by_id: dict[str, dict[str, Any]] = {}
        for tr in traces:
            for c in tr.get("candidates") or []:
                cid = str(c.get("chunk_id") or c.get("id") or "").strip()
                if not cid:
                    continue
                score = float(c.get("score") or 0.0)
                prev = meta_by_id.get(cid)
                if prev is None or score > float(prev.get("score") or 0.0):
                    meta_by_id[cid] = dict(c)
                    meta_by_id[cid]["chunk_id"] = cid

        if not meta_by_id:
            return []

        # trace 里若无正文，从 chunks 表补齐
        need_text = [
            cid
            for cid, meta in meta_by_id.items()
            if not str(meta.get("text") or "").strip()
        ]
        texts: dict[str, str] = {}
        dates: dict[str, str] = {}
        if need_text:
            conn = get_db()
            try:
                ph = ",".join("?" * len(need_text))
                for r in conn.execute(
                    f"SELECT id, date, text FROM chunks WHERE id IN ({ph})",
                    need_text,
                ).fetchall():
                    texts[r["id"]] = r["text"] or ""
                    dates[r["id"]] = r["date"] or ""
            finally:
                conn.close()

        out: list[RetrievedMemory] = []
        for cid, meta in meta_by_id.items():
            text = str(meta.get("text") or "").strip() or texts.get(cid, "")
            if not text.strip():
                continue
            hits = meta.get("matched_sentences") or []
            if not isinstance(hits, list):
                hits = []
            out.append(
                RetrievedMemory(
                    unit_id=cid,
                    chunk_id=cid,
                    score=float(meta.get("score") or 0.0),
                    source=str(meta.get("source") or "prior"),
                    date=str(meta.get("date") or dates.get(cid, "")),
                    text=text,
                    evidence_text=text,
                    matched_sentences=[h for h in hits if isinstance(h, dict)],
                    recall_origin="prior",
                )
            )
        return out

    @staticmethod
    def merge_current_and_prior(
        current: list[RetrievedMemory],
        prior: list[RetrievedMemory],
    ) -> list[RetrievedMemory]:
        """按 chunk_id 去重：本轮覆盖历史；顺序为本轮在前、仅历史的在后。"""
        by_id: dict[str, RetrievedMemory] = {}
        for m in prior:
            key = (m.chunk_id or m.unit_id or "").strip()
            if key:
                by_id[key] = m
        current_keys: list[str] = []
        for m in current:
            key = (m.chunk_id or m.unit_id or "").strip()
            if not key:
                continue
            m.recall_origin = "current"
            by_id[key] = m
            current_keys.append(key)
        seen = set(current_keys)
        ordered: list[RetrievedMemory] = [by_id[k] for k in current_keys]
        for m in prior:
            key = (m.chunk_id or m.unit_id or "").strip()
            if key and key not in seen and key in by_id:
                ordered.append(by_id[key])
                seen.add(key)
        return ordered

    def filter_memories(self, memories: list[RetrievedMemory]) -> list[RetrievedMemory]:
        min_score = self.memory_min_score()
        max_items = self.memory_max_items()
        filtered = [m for m in memories if m.score >= min_score and m.text.strip()]
        # 本轮优先保留：先 current 再 prior，各组内按分排序
        current = sorted(
            [m for m in filtered if m.recall_origin != "prior"],
            key=lambda m: (-m.score, m.date, m.unit_id),
        )
        prior = sorted(
            [m for m in filtered if m.recall_origin == "prior"],
            key=lambda m: (-m.score, m.date, m.unit_id),
        )
        return (current + prior)[:max_items]

    def rank_memories(
        self,
        memories: list[RetrievedMemory],
        *,
        state: ConversationState | None = None,
    ) -> list[RetrievedMemory]:
        """
        Context 侧排序：本轮优先；同分下若 summary/recent 提到日期，略微提升同日记忆。
        """
        if not memories:
            return []
        hint = ""
        if state:
            window = self.session_window_turns()
            recent = self.conversation.recent_messages(state, max_turns=window)
            hint = (state.summary or "") + " " + " ".join(m.content for m in recent[-4:])
        scored: list[tuple[float, int, RetrievedMemory]] = []
        for m in memories:
            bonus = 0.0
            if m.date and m.date in hint:
                bonus = 0.05
            # 本轮加权，避免历史挤掉本轮
            origin_boost = 0.0 if m.recall_origin == "prior" else 1.0
            scored.append(
                (
                    m.score + bonus + origin_boost,
                    0 if m.recall_origin != "prior" else 1,
                    m,
                )
            )
        scored.sort(key=lambda x: (-x[0], x[1], x[2].date, x[2].unit_id))
        return [m for _, _, m in scored]

    def _pack_recent(
        self,
        messages: list[Message],
        max_tokens: int,
    ) -> tuple[list[Message], int]:
        if max_tokens <= 0:
            return [], 0
        kept: list[Message] = []
        used = 0
        for msg in reversed(messages):
            t = estimate_tokens(f"{msg.role}: {msg.content}")
            if used + t > max_tokens and kept:
                break
            if used + t > max_tokens:
                content = fit_text(msg.content, max(1, max_tokens - used - 2))
                kept.append(
                    Message(role=msg.role, content=content, timestamp=msg.timestamp, id=msg.id)
                )
                used = max_tokens
                break
            kept.append(msg)
            used += t
        kept.reverse()
        return kept, used

    def _pack_memories(
        self,
        memories: list[RetrievedMemory],
        max_tokens: int,
    ) -> tuple[list[RetrievedMemory], str, int]:
        if max_tokens <= 0 or not memories:
            return [], "", 0
        lines: list[str] = []
        kept: list[RetrievedMemory] = []
        used = 0
        for m in memories:
            origin_tag = "本轮" if m.recall_origin != "prior" else "窗口内曾召回"
            header = (
                f"[{m.date or '????-??-??'}] id={m.chunk_id or m.unit_id} "
                f"({origin_tag}, score={m.score:.2f}, {m.source or '-'})"
            )
            hits = getattr(m, "matched_sentences", None) or []
            if hits:
                reason_lines = []
                for h in hits[:8]:
                    ht = str(h.get("text") or "").strip()
                    if not ht:
                        continue
                    hs = float(h.get("score") or 0.0)
                    reason_lines.append(f"- ({hs:.2f}) {ht}")
                reasons = "\n".join(reason_lines)
                if reasons:
                    body = f"{header}\n原文：{m.text}\n命中理由：\n{reasons}"
                else:
                    body = f"{header}\n原文：{m.text}"
            else:
                body = f"{header}\n{m.text}"
            line = body
            t = estimate_tokens(line)
            if used + t > max_tokens:
                remain = max_tokens - used
                if remain > 20:
                    line = fit_text(line, remain)
                    lines.append(line)
                    kept.append(m)
                    used = max_tokens
                break
            lines.append(line)
            kept.append(m)
            used += t
        block = (
            "【相关日记记忆】（含本轮检索 + 近窗口内曾召回的 chunk；"
            "仅供本次回答参考，不是聊天记录）\n"
            + "\n\n".join(lines)
        )
        return kept, block, estimate_tokens(block)

    def build(
        self,
        *,
        query: str,
        state: ConversationState,
        memories: Iterable[Candidate | RetrievedMemory | dict[str, Any]] | None = None,
        retrieval_trace: dict[str, Any] | None = None,
        prior_memories: Iterable[RetrievedMemory] | None = None,
    ) -> BuiltContext:
        """
        Context Builder：按流水线组装最终 LLM messages。

        1. System Prompt
        2. Conversation Summary（窗口外旧对话）
        3. Recent Messages（滑动窗口内原文）
        4. Retrieved Memories（本轮 ∪ 窗口内曾召回）
        5. Current User Query
        """
        return self.build_context(
            query=query,
            state=state,
            memories=memories,
            retrieval_trace=retrieval_trace,
            prior_memories=prior_memories,
        )

    def build_context(
        self,
        *,
        query: str,
        state: ConversationState,
        memories: Iterable[Candidate | RetrievedMemory | dict[str, Any]] | None = None,
        retrieval_trace: dict[str, Any] | None = None,
        prior_memories: Iterable[RetrievedMemory] | None = None,
    ) -> BuiltContext:
        budget = self.budget
        window = self.session_window_turns()

        # --- 1. System Prompt ---
        system = fit_text(self.system_prompt(), budget.allot("system"))

        # --- 2. Conversation Summary（溢出窗口的压缩记忆）---
        summary = fit_text(state.summary or "", budget.allot("summary"))

        # --- 3. Recent Messages（滑动窗口）---
        recent_src = self.conversation.recent_messages(state, max_turns=window)
        recent, recent_used = self._pack_recent(recent_src, budget.allot("recent"))

        # --- 4. Retrieved Memories：本轮 ∪ 窗口内曾召回 ---
        current = self.adapt_memories(memories)
        for m in current:
            m.recall_origin = "current"
        if prior_memories is None:
            prior = self.load_prior_memories(
                state.conversation_id, max_turns=window
            )
        else:
            prior = list(prior_memories)
        merged = self.merge_current_and_prior(current, prior)
        filtered = self.filter_memories(merged)
        ranked = self.rank_memories(filtered, state=state)
        kept_mem, mem_block, mem_used = self._pack_memories(
            ranked, budget.allot("memories")
        )

        # --- 5. Current User Query ---
        q_text = fit_text(query, budget.allot("query"))

        # 组装 messages：system 承载指令 + 摘要；recent 为多轮原文；
        # 记忆块作为独立 system 段插在当前 query 之前（不写入会话历史）。
        system_parts = [system]
        if summary:
            system_parts.append(
                "【对话摘要】（滑动窗口之外的较早对话，已压缩）\n" + summary
            )
        system_content = "\n\n".join(p for p in system_parts if p)

        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        for m in recent:
            if m.role in ("user", "assistant"):
                messages.append({"role": m.role, "content": m.content})
        if mem_block:
            messages.append({"role": "system", "content": mem_block})
        messages.append({"role": "user", "content": q_text})

        total_est = sum(estimate_tokens(m["content"]) for m in messages)
        return BuiltContext(
            messages=messages,
            system=system,
            summary=summary,
            recent=recent,
            memories=kept_mem,
            query=q_text,
            token_estimate=total_est,
            budget_used={
                "system": estimate_tokens(system),
                "summary": estimate_tokens(summary),
                "recent": recent_used,
                "memories": mem_used,
                "query": estimate_tokens(q_text),
                "total": total_est,
            },
            retrieval_trace=retrieval_trace or {},
        )

    def ensure_summary(self, state: ConversationState) -> str:
        """
        会话短期记忆维护：超出滑动窗口的消息压缩进 summary。

        在 build_context 之前调用，避免窗口外消息既不进 recent 又不在摘要里。
        """
        cfg = self._cfg()
        window = self.session_window_turns()
        overflow, _recent = self.conversation.split_session_window(
            state, max_turns=window
        )
        if not overflow:
            return state.summary

        covered = int(getattr(state, "summary_upto", 0) or 0)
        # 溢出已全部纳入摘要 → 跳过
        if covered >= len(overflow) and state.summary:
            return state.summary

        # 可选节流：未覆盖溢出不足 N 条时暂不打 LLM，但仍把未覆盖原文拼进摘要区，避免丢上下文
        step = int(cfg.get("summarize_every_messages", 1))
        uncovered = overflow[covered:] if covered < len(overflow) else overflow[-40:]
        if (
            state.summary
            and step > 1
            and len(uncovered) < step
            and covered > 0
        ):
            # 轻量回退：已有摘要 + 未压缩溢出的短摘录，保证窗口外内容仍可见
            snippets = [f"{m.role}:{m.content[:80]}" for m in uncovered]
            interim = (state.summary.strip() + "；" + "；".join(snippets)).strip("；")
            state.summary = fit_text(interim, self.budget.allot("summary") or 400)
            return state.summary

        # 压缩尚未纳入摘要的溢出段（带上已有摘要做增量合并）
        transcript = "\n".join(f"{m.role}: {m.content}" for m in uncovered[-40:])
        prev = state.summary.strip()
        prompt = f"""你是对话摘要助手。请把下列较早聊天记录压缩成简洁中文摘要（不超过 200 字）。
保留：用户目标、已确认事实、重要专名/日期、未决问题。不要编造。

{f"已有摘要：{prev}" if prev else ""}

待压缩对话：
{transcript}

只输出摘要正文，不要标题或解释。"""

        summary = ""
        try:
            from src.llm import get_llm_client, get_llm_model

            client = get_llm_client("tags")
            resp = client.chat.completions.create(
                model=get_llm_model("tags"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            summary = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            print(f"  [warn] LLM 摘要失败，回退规则压缩: {exc}")
            snippets = [f"{m.role}:{m.content[:60]}" for m in uncovered[-12:]]
            if prev:
                summary = prev + "；" + "；".join(snippets)
            else:
                summary = "；".join(snippets)

        summary = fit_text(summary, self.budget.allot("summary") or 400)
        if not summary:
            return state.summary

        self.conversation.update_summary(
            state.conversation_id,
            summary,
            summary_upto=len(overflow),
        )
        state.summary = summary
        state.summary_upto = len(overflow)
        return summary

    def maybe_update_summary(self, state: ConversationState) -> str:
        """兼容旧名：等价于 ensure_summary。"""
        return self.ensure_summary(state)
