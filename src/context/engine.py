"""Context Engine：组合 Conversation + Retrieved Memories → LLM messages。"""

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
from src.store import load_config

DEFAULT_SYSTEM = """你是用户的陪伴型助手：可以闲聊生活、想法与日常，也可以在有日记材料时帮忙回忆往事。

系统有时会附上相关日记片段和对话上下文。有材料时，把它们当作「记得的事」自然融入回答，涉及具体经历时顺手带上日期；没有材料或材料对不上时，照常陪聊、共情、追问，不要因此拒答或说「无法提供更多信息」。

若记忆块含「相关视角」，那是检索到的语义视角；「原文证据」是日记原文。可综合推理，抽象结论需有视角或原文支撑。

仅在用户明确追问「日记里有没有 / 当时具体怎样」且材料不足时，才说明日记里没找到可靠依据——此时仍可继续聊，别硬编日记事实。"""


class ContextEngine:
    """
    不负责检索；只消费 Memory Engine 的 Candidate / 已 hydrate 的记忆，
    并结合 ConversationState 构建最终 Prompt。
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

    def recent_turns(self) -> int:
        raw = os.getenv("CONTEXT_RECENT_TURNS", "").strip()
        if raw:
            return int(raw)
        return int(self._cfg().get("recent_message_turns", 8))

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

        # dict（query.retrieve_chunks 返回）
        out: list[RetrievedMemory] = []
        for m in items:
            if isinstance(m, RetrievedMemory):
                out.append(m)
            elif isinstance(m, Candidate):
                out.append(RetrievedMemory.from_candidate(m))
            elif isinstance(m, dict):
                out.append(RetrievedMemory.from_hydrated(m))
        return out

    def filter_memories(self, memories: list[RetrievedMemory]) -> list[RetrievedMemory]:
        min_score = self.memory_min_score()
        max_items = self.memory_max_items()
        filtered = [m for m in memories if m.score >= min_score and m.text.strip()]
        filtered.sort(key=lambda m: (-m.score, m.date, m.chunk_id))
        return filtered[:max_items]

    def rank_memories(
        self,
        memories: list[RetrievedMemory],
        *,
        state: ConversationState | None = None,
    ) -> list[RetrievedMemory]:
        """
        Context 侧排序：默认保持检索分；若 summary/recent 提到日期，略微提升同日记忆。
        """
        if not memories:
            return []
        hint = ""
        if state:
            hint = (state.summary or "") + " " + " ".join(
                m.content for m in state.messages[-4:]
            )
        scored: list[tuple[float, RetrievedMemory]] = []
        for m in memories:
            bonus = 0.0
            if m.date and m.date in hint:
                bonus = 0.05
            scored.append((m.score + bonus, m))
        scored.sort(key=lambda x: (-x[0], x[1].date, x[1].chunk_id))
        return [m for _, m in scored]

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
            if m.matched_views:
                view_lines = []
                for v in m.matched_views[:3]:
                    vtype = v.get("view_type") or v.get("type") or "view"
                    vtext = str(v.get("content") or "")[:200]
                    view_lines.append(f"- [{vtype}] {vtext}")
                body = (
                    f"[{m.date or '????-??-??'}] chunk_id={m.chunk_id} "
                    f"(score={m.score:.2f}, {m.source or '-'})\n"
                    f"相关视角：\n" + "\n".join(view_lines) + "\n"
                    f"原文证据：\n{m.text}"
                )
            else:
                body = (
                    f"[{m.date or '????-??-??'}] "
                    f"(score={m.score:.2f}, {m.source or '-'}) {m.text}"
                )
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
        block = "相关记忆片段：\n" + "\n\n".join(lines)
        return kept, block, estimate_tokens(block)

    def build(
        self,
        *,
        query: str,
        state: ConversationState,
        memories: Iterable[Candidate | RetrievedMemory | dict[str, Any]] | None = None,
        retrieval_trace: dict[str, Any] | None = None,
    ) -> BuiltContext:
        budget = self.budget
        system = fit_text(self.system_prompt(), budget.allot("system"))
        summary = fit_text(state.summary or "", budget.allot("summary"))

        recent_src = self.conversation.recent_messages(
            state, max_turns=self.recent_turns()
        )
        recent, recent_used = self._pack_recent(recent_src, budget.allot("recent"))

        adapted = self.adapt_memories(memories)
        filtered = self.filter_memories(adapted)
        ranked = self.rank_memories(filtered, state=state)
        kept_mem, mem_block, mem_used = self._pack_memories(
            ranked, budget.allot("memories")
        )

        q_text = fit_text(query, budget.allot("query"))

        # 组装 OpenAI messages：system 合并 summary + memories 说明；recent 为多轮；最后 user=query
        system_parts = [system]
        if summary:
            system_parts.append(f"对话摘要：\n{summary}")
        if mem_block:
            system_parts.append(mem_block)
        system_content = "\n\n".join(p for p in system_parts if p)

        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        for m in recent:
            if m.role in ("user", "assistant"):
                messages.append({"role": m.role, "content": m.content})
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

    def maybe_update_summary(self, state: ConversationState) -> str:
        """消息过多时用 LLM 压缩较早对话为 summary（不写入召回记忆）。"""
        cfg = self._cfg()
        threshold = int(cfg.get("summarize_after_messages", 20))
        if len(state.messages) < threshold:
            return state.summary

        keep_n = self.recent_turns() * 2
        older = state.messages[:-keep_n] if keep_n < len(state.messages) else []
        if not older:
            return state.summary

        # 已摘要过且没有足够新的旧消息 → 跳过，避免每轮打 LLM
        step = int(cfg.get("summarize_every_messages", 10))
        covered = int(getattr(state, "summary_upto", 0) or 0)
        if len(older) - covered < max(1, step) and state.summary:
            return state.summary

        transcript = "\n".join(
            f"{m.role}: {m.content}" for m in older[-40:]
        )
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

            client = get_llm_client("tags")  # 轻量模型
            resp = client.chat.completions.create(
                model=get_llm_model("tags"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            summary = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            print(f"  [warn] LLM 摘要失败，回退规则压缩: {exc}")
            snippets = [f"{m.role}:{m.content[:60]}" for m in older[-12:]]
            summary = "；".join(snippets)

        summary = fit_text(summary, self.budget.allot("summary") or 400)
        if not summary:
            return state.summary

        self.conversation.update_summary(
            state.conversation_id,
            summary,
            summary_upto=len(older),
        )
        state.summary = summary
        state.summary_upto = len(older)
        return summary
