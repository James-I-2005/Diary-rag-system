"""ReAct Query Agent：先分析拆解，再调 grep/rag 纯函数工具。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.context.models import ConversationState
from src.llm import get_llm_client, get_llm_model
from src.query_agent.models import StructuredQuery
from src.store import load_config, resolve_path
from src.tools import call_tool
from src.tools.evidence import merge_chunk_evidence

_PROMPT_PATH = Path(__file__).with_name("react_prompt.md")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentRetrievalResult:
    """一轮召回结果：结构化分析 + chunk 证据 + 时间线。"""

    structured: StructuredQuery
    chunks: list[dict[str, Any]] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""


class _Timeline:
    """按发生顺序追加事件，供调试审阅。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def add(self, event: str, **payload: Any) -> dict[str, Any]:
        row = {
            "t": len(self.events) + 1,
            "event": event,
            "at": _now(),
            **payload,
        }
        self.events.append(row)
        return row

    def tool_trace(self) -> list[dict[str, Any]]:
        """兼容旧字段：仅抽出 tool 事件。"""
        out: list[dict[str, Any]] = []
        for e in self.events:
            if e.get("event") != "tool":
                continue
            result = e.get("result") or {}
            out.append(
                {
                    "tool": e.get("tool"),
                    "args": e.get("args") or {},
                    "reason": e.get("why") or "",
                    "ok": result.get("ok"),
                    "count": result.get("count", 0),
                    "error": result.get("error"),
                }
            )
        return out

    def as_text(self) -> str:
        lines: list[str] = []
        for e in self.events:
            t = e.get("t")
            kind = e.get("event")
            if kind == "analyze":
                lines.append(f"[{t}] 分析")
                thought = (e.get("thought") or "").strip()
                if thought:
                    lines.append(f"    判断：{thought}")
                lines.append(f"    决策：{e.get('decision')}")
                for p in e.get("plan") or []:
                    ch = p.get("channel")
                    if ch == "grep":
                        lines.append(
                            f"    计划 grep terms={p.get('terms')} — {p.get('reason') or ''}"
                        )
                    else:
                        lines.append(
                            f"    计划 rag themes={p.get('themes') or p.get('query')} — {p.get('reason') or ''}"
                        )
            elif kind == "tool":
                res = e.get("result") or {}
                lines.append(
                    f"[{t}] 调用 {e.get('tool')} → {res.get('count', 0)} 条"
                    + (f"（失败：{res.get('error')}）" if res.get("error") else "")
                )
                why = (e.get("why") or "").strip()
                if why:
                    lines.append(f"    原因：{why}")
                args = e.get("args") or {}
                if args.get("terms"):
                    lines.append(f"    参数 terms={args['terms']}")
                if args.get("themes"):
                    lines.append(f"    参数 themes={args['themes']}")
                for hit in (res.get("preview") or [])[:5]:
                    snip = (hit.get("snippet") or hit.get("text") or "")[:80]
                    lines.append(
                        f"    · [{hit.get('date') or '?'}] {hit.get('chunk_id')} {snip}"
                    )
            elif kind == "react":
                lines.append(f"[{t}] 观察后决策 → {e.get('decision')}")
                thought = (e.get("thought") or "").strip()
                if thought:
                    lines.append(f"    判断：{thought}")
                reason = (e.get("reason") or "").strip()
                if reason:
                    lines.append(f"    说明：{reason}")
                for p in e.get("plan") or []:
                    ch = p.get("channel")
                    if ch == "grep":
                        lines.append(f"    续调 grep terms={p.get('terms')}")
                    else:
                        lines.append(
                            f"    续调 rag themes={p.get('themes') or p.get('query')}"
                        )
            elif kind == "done":
                fin = e.get("final") or {}
                lines.append(
                    f"[{t}] 结束 stop={e.get('stop_reason')} 最终证据 {fin.get('n_chunks', 0)} 条"
                )
                for c in (fin.get("chunks") or [])[:8]:
                    prev = (c.get("preview") or "")[:80]
                    lines.append(
                        f"    · [{c.get('date') or '?'}] ({c.get('source')}) "
                        f"{c.get('id')} score={c.get('score')} {prev}"
                    )
            else:
                lines.append(f"[{t}] {kind}: {json.dumps(e, ensure_ascii=False)[:200]}")
        return "\n".join(lines)


def _cfg() -> dict[str, Any]:
    return load_config().get("query_agent") or {}


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            return data
    raise ValueError(f"无法解析 Agent JSON: {raw[:400]!r}")


def _load_react_prompt() -> str:
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return (
        "分析用户日记问题，输出 JSON："
        '{"stage":"analyze","need_retrieval":true,"parts":[{"channel":"grep|rag",...}],'
        '"decision":"call_tools|answer"}'
    )


def _tool_preview(out: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    """从工具返回里抽出可审阅摘要。"""
    hits = out.get("hits")
    if isinstance(hits, list) and hits:
        rows = []
        for h in hits[:limit]:
            rows.append(
                {
                    "chunk_id": h.get("chunk_id"),
                    "date": h.get("date") or "",
                    "snippet": h.get("snippet") or "",
                    "matched_terms": h.get("matched_terms") or [],
                    "score": h.get("score"),
                }
            )
        return rows
    chunks = out.get("chunks") or []
    rows = []
    for c in chunks[:limit]:
        text = (c.get("text") or "")[:120].replace("\n", " ")
        rows.append(
            {
                "chunk_id": c.get("chunk_id") or c.get("id"),
                "date": c.get("date") or "",
                "snippet": text,
                "score": c.get("score"),
                "source": c.get("source"),
            }
        )
    return rows


def _final_chunk_preview(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for c in chunks:
        text = (c.get("text") or "")[:120].replace("\n", " ")
        rows.append(
            {
                "id": c.get("id") or c.get("chunk_id"),
                "date": c.get("date") or "",
                "score": c.get("score"),
                "source": c.get("source"),
                "preview": text,
            }
        )
    return rows


class ReactQueryAgent:
    """
    中枢：analyze → tools(grep|rag_search) → react → …
    工具为纯函数（src.tools），无第三方 Agent 框架。
    LLM 输入与 Answer 共用 ContextEngine 流水线（摘要/窗口对话/曾召回记忆）。
    """

    def __init__(self, context_engine: Any | None = None) -> None:
        self._cfg = _cfg()
        self.context_engine = context_engine
        self._last_agent_messages_preview: list[dict[str, Any]] = []

    def mode(self) -> str:
        return str(self._cfg.get("mode") or "react").strip().lower()

    def llm_role(self) -> str:
        return str(self._cfg.get("llm_role") or "tags")

    def max_tool_steps(self) -> int:
        return max(1, min(8, int(self._cfg.get("max_tool_steps", 4))))

    def evidence_top_k(self) -> int:
        try:
            from src.tag_retrieve import resolve_retrieval_config

            return max(1, int(resolve_retrieval_config().top_k))
        except Exception:
            return max(1, int(self._cfg.get("evidence_top_k", 5)))

    def grep_top_k(self) -> int:
        tools = self._cfg.get("tools") or {}
        grep = tools.get("grep") or {}
        return max(1, int(grep.get("top_k", 20)))

    def rag_top_k(self) -> int:
        tools = self._cfg.get("tools") or {}
        rag = tools.get("rag_search") or {}
        if "top_k" in rag:
            return max(1, int(rag["top_k"]))
        return self.evidence_top_k()

    def retrieve(
        self,
        raw_query: str,
        *,
        state: ConversationState | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        dates: list[str] | None = None,
        scheme: str | None = None,
    ) -> AgentRetrievalResult:
        from src.engine.date_range import normalize_date_list

        original = (raw_query or "").strip()
        df = (date_from or "").strip()
        dt = (date_to or "").strip()
        dset = normalize_date_list(dates)
        filters = {
            "date_from": None if dset else (df or None),
            "date_to": None if dset else (dt or None),
            "dates": dset or None,
        }
        tl = _Timeline()

        if not original:
            sq = StructuredQuery(
                original_query=raw_query or "",
                rewritten_query="",
                need_retrieval=False,
                source="react",
                dates=dset,
                date_from=df if not dset else "",
                date_to=dt if not dset else "",
            )
            tl.add("done", stop_reason="empty_query", final={"n_chunks": 0, "chunks": []})
            self._save_debug(sq, {}, tl, [], "empty_query", state, filters)
            return AgentRetrievalResult(
                structured=sq, timeline=tl.events, stop_reason="empty_query"
            )

        chunk_lists: list[list[dict[str, Any]]] = []
        analysis: dict[str, Any] = {}

        # —— 强制第一步：分析 ——
        try:
            plan = self._llm_turn(
                stage="analyze",
                question=original,
                state=state,
                filters=filters,
                evidence_chunks=[],
            )
        except Exception as exc:
            print(f"  [warn] React analyze 失败，降级 rag: {exc}")
            plan = {
                "stage": "analyze",
                "need_retrieval": True,
                "analysis": f"analyze_failed:{exc}",
                "decision": "call_tools",
                "parts": [
                    {
                        "channel": "rag",
                        "themes": [original],
                        "reason": "fallback",
                    }
                ],
            }

        analysis = {
            "text": plan.get("analysis") or "",
            "parts": plan.get("parts") or [],
            "need_retrieval": bool(plan.get("need_retrieval", True)),
        }
        decision0 = str(plan.get("decision") or "call_tools").strip()
        tl.add(
            "analyze",
            thought=analysis["text"],
            need_retrieval=analysis["need_retrieval"],
            decision=decision0,
            plan=analysis["parts"],
        )

        themes_acc: list[str] = []
        grep_terms_acc: list[str] = []

        if not analysis["need_retrieval"] or decision0 == "answer":
            sq = self._to_structured(
                original,
                themes_acc,
                grep_terms_acc,
                analysis,
                df,
                dt,
                need=False,
                dates=dset,
            )
            tl.add(
                "done",
                stop_reason="no_retrieval",
                final={"n_chunks": 0, "chunks": []},
            )
            self._save_debug(sq, analysis, tl, [], "no_retrieval", state, filters)
            return AgentRetrievalResult(
                structured=sq,
                chunks=[],
                analysis=analysis,
                timeline=tl.events,
                tool_trace=tl.tool_trace(),
                stop_reason="no_retrieval",
            )

        # 执行分析给出的 parts
        self._run_parts(
            plan.get("parts") or [],
            filters=filters,
            scheme=scheme,
            chunk_lists=chunk_lists,
            timeline=tl,
            themes_acc=themes_acc,
            grep_terms_acc=grep_terms_acc,
        )

        # —— 后续 ReAct 步 ——
        stop_reason = "max_steps"
        steps_used = 1  # 分析后的首轮工具算一步
        max_steps = self.max_tool_steps()

        while steps_used < max_steps:
            evidence = merge_chunk_evidence(
                *chunk_lists, top_k=self.evidence_top_k() * 2
            )
            try:
                thought = self._llm_turn(
                    stage="react",
                    question=original,
                    state=state,
                    filters=filters,
                    evidence_chunks=evidence,
                    prior_analysis=analysis.get("text") or "",
                )
            except Exception as exc:
                print(f"  [warn] React step 失败，停止: {exc}")
                stop_reason = f"react_error:{exc}"
                tl.add(
                    "react",
                    thought=f"error: {exc}",
                    decision="answer",
                    reason=stop_reason,
                    plan=[],
                )
                break

            decision = str(thought.get("decision") or "answer").strip()
            parts = thought.get("parts") or []
            tl.add(
                "react",
                thought=str(thought.get("analysis") or thought.get("thought") or ""),
                decision=decision,
                reason=str(thought.get("reason") or ""),
                plan=parts if decision == "call_tools" else [],
            )

            if decision != "call_tools":
                stop_reason = str(thought.get("reason") or "answer")
                break

            if not parts:
                stop_reason = "empty_parts"
                break

            before = len(tl.tool_trace())
            self._run_parts(
                parts,
                filters=filters,
                scheme=scheme,
                chunk_lists=chunk_lists,
                timeline=tl,
                themes_acc=themes_acc,
                grep_terms_acc=grep_terms_acc,
            )
            if len(tl.tool_trace()) == before:
                stop_reason = "no_new_tools"
                break
            steps_used += 1
            stop_reason = "continue"
        else:
            stop_reason = "max_steps"

        final_chunks = merge_chunk_evidence(
            *chunk_lists, top_k=self.evidence_top_k()
        )
        sq = self._to_structured(
            original,
            themes_acc,
            grep_terms_acc,
            analysis,
            df,
            dt,
            need=True,
            dates=dset,
        )
        sq.meta["timeline"] = tl.events
        sq.meta["stop_reason"] = stop_reason
        sq.meta["analysis"] = analysis

        tl.add(
            "done",
            stop_reason=stop_reason,
            final={
                "n_chunks": len(final_chunks),
                "chunks": _final_chunk_preview(final_chunks),
            },
        )
        self._save_debug(
            sq, analysis, tl, final_chunks, stop_reason, state, filters
        )
        return AgentRetrievalResult(
            structured=sq,
            chunks=final_chunks,
            analysis=analysis,
            timeline=tl.events,
            tool_trace=tl.tool_trace(),
            stop_reason=stop_reason,
        )

    def _run_parts(
        self,
        parts: list[Any],
        *,
        filters: dict[str, Any],
        scheme: str | None,
        chunk_lists: list[list[dict[str, Any]]],
        timeline: _Timeline,
        themes_acc: list[str],
        grep_terms_acc: list[str],
    ) -> None:
        for part in parts:
            if not isinstance(part, dict):
                continue
            channel = str(part.get("channel") or "").strip().lower()
            reason = str(part.get("reason") or "")
            if channel == "grep":
                terms = [
                    str(t).strip()
                    for t in (part.get("terms") or [])
                    if str(t).strip()
                ]
                if not terms:
                    continue
                for t in terms:
                    if t not in grep_terms_acc:
                        grep_terms_acc.append(t)
                out = call_tool(
                    "grep",
                    terms=terms,
                    date_from=filters.get("date_from"),
                    date_to=filters.get("date_to"),
                    dates=filters.get("dates"),
                    top_k=self.grep_top_k(),
                )
                chunk_lists.append(out.get("chunks") or [])
                timeline.add(
                    "tool",
                    tool="grep",
                    why=reason,
                    args={"terms": terms},
                    result={
                        "ok": out.get("ok", False),
                        "count": out.get("count", 0),
                        "error": out.get("error"),
                        "preview": _tool_preview(out),
                    },
                )
            elif channel in {"rag", "rag_search", "embedding"}:
                themes = [
                    str(t).strip()
                    for t in (part.get("themes") or [])
                    if str(t).strip()
                ]
                query = str(part.get("query") or "").strip()
                if not themes and query:
                    themes = [query]
                if not themes:
                    continue
                for t in themes:
                    if t not in themes_acc:
                        themes_acc.append(t)
                out = call_tool(
                    "rag_search",
                    themes=themes[:3],
                    query=query,
                    date_from=filters.get("date_from"),
                    date_to=filters.get("date_to"),
                    dates=filters.get("dates"),
                    top_k=self.rag_top_k(),
                    scheme=scheme,
                )
                chunk_lists.append(out.get("chunks") or [])
                timeline.add(
                    "tool",
                    tool="rag_search",
                    why=reason,
                    args={"themes": themes[:3]},
                    result={
                        "ok": out.get("ok", False),
                        "count": out.get("count", 0),
                        "error": out.get("error"),
                        "preview": _tool_preview(out),
                    },
                )

    def _llm_turn(
        self,
        *,
        stage: str,
        question: str,
        state: ConversationState | None,
        filters: dict[str, Any],
        evidence_chunks: list[dict[str, Any]] | None = None,
        prior_analysis: str = "",
    ) -> dict[str, Any]:
        """
        与 Answer 共用 ContextEngine 流水线：
        System(Agent) + 摘要 + 窗口对话 + 记忆(本轮工具∪窗口曾召回) + 当前问题。
        """
        role = self.llm_role()
        client = get_llm_client(role)
        messages = self._build_agent_messages(
            stage=stage,
            question=question,
            state=state,
            filters=filters,
            evidence_chunks=evidence_chunks or [],
            prior_analysis=prior_analysis,
        )

        resp = client.chat.completions.create(
            model=get_llm_model(role),
            messages=messages,
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json_object(raw)
        data["_raw"] = raw[:1000]
        return data

    def _build_agent_messages(
        self,
        *,
        stage: str,
        question: str,
        state: ConversationState | None,
        filters: dict[str, Any],
        evidence_chunks: list[dict[str, Any]],
        prior_analysis: str = "",
    ) -> list[dict[str, str]]:
        task_lines = [
            "【Query Agent 本轮任务】",
            f"当前阶段：{stage}",
        ]
        dset = filters.get("dates") or []
        if dset:
            preview = "、".join(dset[:12])
            more = f" 等{len(dset)}天" if len(dset) > 12 else f"（{len(dset)}天）"
            task_lines.append(f"日期过滤（集合）：{preview}{more}")
        else:
            task_lines.append(
                f"日期过滤：from={filters.get('date_from') or '无'} "
                f"to={filters.get('date_to') or '无'}"
            )
        if prior_analysis:
            task_lines.append(f"先前分析：{prior_analysis}")
        task_lines.append(
            "请结合上方对话摘要、最近对话、日记记忆理解指代"
            "（如「其他的呢」「再列一些」）；"
            "已出现在记忆块中的 chunk 视为已展示，优先补新证据。"
        )
        task_lines.append("请只输出一个 JSON 对象（格式见系统说明）。")
        task_block = "\n".join(task_lines)

        if state is not None:
            engine = self.context_engine
            if engine is None:
                from src.context.engine import ContextEngine

                engine = ContextEngine()
                self.context_engine = engine
            built = engine.build_context(
                query=question,
                state=state,
                memories=evidence_chunks,
                system_override=_load_react_prompt(),
                extra_system_blocks=[task_block],
            )
            # 供 debug：记录与 Answer 同构的输入预览
            self._last_agent_messages_preview = [
                {"role": m["role"], "chars": len(m["content"]), "head": m["content"][:200]}
                for m in built.messages
            ]
            return list(built.messages)

        # 无会话时退化为：Agent 系统 + 任务 + 用户问题
        return [
            {"role": "system", "content": _load_react_prompt()},
            {"role": "system", "content": task_block},
            {"role": "user", "content": question},
        ]

    def _evidence_summary(self, chunks: list[dict[str, Any]]) -> str:
        if not chunks:
            return "（空）"
        lines = []
        for c in chunks[:8]:
            date = c.get("date") or "?"
            src = c.get("source") or "?"
            terms = c.get("matched_terms") or []
            text = (c.get("text") or "")[:120].replace("\n", " ")
            extra = f" terms={terms}" if terms else ""
            lines.append(f"- [{date}] ({src}{extra}) {text}…")
        return "\n".join(lines)

    def _to_structured(
        self,
        original: str,
        themes: list[str],
        grep_terms: list[str],
        analysis: dict[str, Any],
        date_from: str,
        date_to: str,
        *,
        need: bool,
        dates: list[str] | None = None,
    ) -> StructuredQuery:
        from src.engine.date_range import normalize_date_list

        themes_u = themes[:3] or ([original] if need else [])
        dset = normalize_date_list(dates)
        return StructuredQuery(
            original_query=original,
            rewritten_query="；".join(themes_u) if themes_u else original,
            query_sentences=themes_u,
            need_retrieval=need,
            retrieval_plan=["embedding"] if need else [],
            embedding_query="\n".join(themes_u),
            source="react",
            dates=dset,
            date_from=date_from if not dset else "",
            date_to=date_to if not dset else "",
            meta={
                "grep_terms": grep_terms,
                "analysis_text": analysis.get("text") or "",
                "parts": analysis.get("parts") or [],
            },
        )

    def _save_debug(
        self,
        structured: StructuredQuery,
        analysis: dict[str, Any],
        timeline: _Timeline,
        chunks: list[dict[str, Any]],
        stop_reason: str,
        state: ConversationState | None,
        filters: dict[str, Any],
    ) -> None:
        if not bool(self._cfg.get("save_debug_json", True)):
            return
        out = resolve_path(self._cfg.get("debug_path", "data/last_query.json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        # 主读：timeline / timeline_text；其余字段收拢，避免散乱
        payload = {
            "built_at": _now(),
            "mode": "react",
            "query": structured.original_query,
            "dates": filters.get("dates") or [],
            "date_from": filters.get("date_from") or "",
            "date_to": filters.get("date_to") or "",
            "conversation_id": state.conversation_id if state else None,
            "stop_reason": stop_reason,
            "timeline_text": timeline.as_text(),
            "timeline": timeline.events,
            "agent_messages_preview": self._last_agent_messages_preview,
            "result": {
                "n_chunks": len(chunks),
                "chunk_ids": [c.get("id") for c in chunks],
                "chunks": _final_chunk_preview(chunks),
            },
            "structured_query": {
                "need_retrieval": structured.need_retrieval,
                "query_themes": structured.query_themes,
                "grep_terms": (structured.meta or {}).get("grep_terms") or [],
                "analysis": analysis.get("text") or "",
            },
        }
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
