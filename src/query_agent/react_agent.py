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


@dataclass
class AgentRetrievalResult:
    """一轮召回结果：结构化分析 + chunk 证据 + 工具轨迹。"""

    structured: StructuredQuery
    chunks: list[dict[str, Any]] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""


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


class ReactQueryAgent:
    """
    中枢：analyze → tools(grep|rag_search) → react → …
    工具为纯函数（src.tools），无第三方 Agent 框架。
    """

    def __init__(self) -> None:
        self._cfg = _cfg()

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
        scheme: str | None = None,
    ) -> AgentRetrievalResult:
        original = (raw_query or "").strip()
        df = (date_from or "").strip()
        dt = (date_to or "").strip()
        filters = {"date_from": df or None, "date_to": dt or None}

        if not original:
            sq = StructuredQuery(
                original_query=raw_query or "",
                rewritten_query="",
                need_retrieval=False,
                source="react",
                date_from=df,
                date_to=dt,
            )
            return AgentRetrievalResult(
                structured=sq, stop_reason="empty_query"
            )

        tool_trace: list[dict[str, Any]] = []
        chunk_lists: list[list[dict[str, Any]]] = []
        analysis: dict[str, Any] = {}

        # —— 强制第一步：分析 ——
        try:
            plan = self._llm_turn(
                stage="analyze",
                question=original,
                state=state,
                evidence_summary="",
                filters=filters,
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

        themes_acc: list[str] = []
        grep_terms_acc: list[str] = []

        if not analysis["need_retrieval"] or plan.get("decision") == "answer":
            sq = self._to_structured(
                original,
                themes_acc,
                grep_terms_acc,
                analysis,
                df,
                dt,
                need=False,
            )
            self._save_debug(sq, analysis, tool_trace, [], "no_retrieval", state)
            return AgentRetrievalResult(
                structured=sq,
                chunks=[],
                analysis=analysis,
                tool_trace=tool_trace,
                stop_reason="no_retrieval",
            )

        # 执行分析给出的 parts
        self._run_parts(
            plan.get("parts") or [],
            filters=filters,
            scheme=scheme,
            chunk_lists=chunk_lists,
            tool_trace=tool_trace,
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
            summary = self._evidence_summary(evidence)
            try:
                thought = self._llm_turn(
                    stage="react",
                    question=original,
                    state=state,
                    evidence_summary=summary,
                    filters=filters,
                    prior_analysis=analysis.get("text") or "",
                )
            except Exception as exc:
                print(f"  [warn] React step 失败，停止: {exc}")
                stop_reason = f"react_error:{exc}"
                break

            decision = str(thought.get("decision") or "answer").strip()
            if decision != "call_tools":
                stop_reason = str(thought.get("reason") or "answer")
                break

            parts = thought.get("parts") or []
            if not parts:
                stop_reason = "empty_parts"
                break

            before = len(tool_trace)
            self._run_parts(
                parts,
                filters=filters,
                scheme=scheme,
                chunk_lists=chunk_lists,
                tool_trace=tool_trace,
                themes_acc=themes_acc,
                grep_terms_acc=grep_terms_acc,
            )
            if len(tool_trace) == before:
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
        )
        sq.meta["tool_trace"] = tool_trace
        sq.meta["stop_reason"] = stop_reason
        sq.meta["analysis"] = analysis

        self._save_debug(
            sq, analysis, tool_trace, final_chunks, stop_reason, state
        )
        return AgentRetrievalResult(
            structured=sq,
            chunks=final_chunks,
            analysis=analysis,
            tool_trace=tool_trace,
            stop_reason=stop_reason,
        )

    def _run_parts(
        self,
        parts: list[Any],
        *,
        filters: dict[str, Any],
        scheme: str | None,
        chunk_lists: list[list[dict[str, Any]]],
        tool_trace: list[dict[str, Any]],
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
                    top_k=self.grep_top_k(),
                )
                chunk_lists.append(out.get("chunks") or [])
                tool_trace.append(
                    {
                        "tool": "grep",
                        "args": {"terms": terms},
                        "reason": reason,
                        "ok": out.get("ok", False),
                        "count": out.get("count", 0),
                        "error": out.get("error"),
                    }
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
                    top_k=self.rag_top_k(),
                    scheme=scheme,
                )
                chunk_lists.append(out.get("chunks") or [])
                tool_trace.append(
                    {
                        "tool": "rag_search",
                        "args": {"themes": themes[:3]},
                        "reason": reason,
                        "ok": out.get("ok", False),
                        "count": out.get("count", 0),
                        "error": out.get("error"),
                    }
                )

    def _llm_turn(
        self,
        *,
        stage: str,
        question: str,
        state: ConversationState | None,
        evidence_summary: str,
        filters: dict[str, Any],
        prior_analysis: str = "",
    ) -> dict[str, Any]:
        role = self.llm_role()
        client = get_llm_client(role)
        user_parts = [
            f"当前阶段：{stage}",
            f"用户问题：{question}",
            f"日期过滤：from={filters.get('date_from') or '无'} to={filters.get('date_to') or '无'}",
        ]
        if prior_analysis:
            user_parts.append(f"先前分析：{prior_analysis}")
        if evidence_summary:
            user_parts.append(f"已召回证据摘要：\n{evidence_summary}")
        else:
            user_parts.append("已召回证据摘要：（尚无）")
        if state and state.summary.strip():
            user_parts.append(f"会话摘要：{state.summary.strip()[:500]}")
        user_parts.append("请只输出一个 JSON 对象。")

        resp = client.chat.completions.create(
            model=get_llm_model(role),
            messages=[
                {"role": "system", "content": _load_react_prompt()},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json_object(raw)
        data["_raw"] = raw[:1000]
        return data

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
    ) -> StructuredQuery:
        themes_u = themes[:3] or ([original] if need else [])
        return StructuredQuery(
            original_query=original,
            rewritten_query="；".join(themes_u) if themes_u else original,
            query_sentences=themes_u,
            need_retrieval=need,
            retrieval_plan=["embedding"] if need else [],
            embedding_query="\n".join(themes_u),
            source="react",
            date_from=date_from,
            date_to=date_to,
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
        tool_trace: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
        stop_reason: str,
        state: ConversationState | None,
    ) -> None:
        if not bool(self._cfg.get("save_debug_json", True)):
            return
        out = resolve_path(self._cfg.get("debug_path", "data/last_query.json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "mode": "react",
            "conversation_id": state.conversation_id if state else None,
            "stop_reason": stop_reason,
            "analysis": analysis,
            "tool_trace": tool_trace,
            "chunk_ids": [c.get("id") for c in chunks],
            "n_chunks": len(chunks),
            **structured.to_dict(),
        }
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
