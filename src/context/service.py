"""Context 服务：一轮对话 = Conversation + Memory Engine + ContextEngine + LLM。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.context.conversation import ConversationManager
from src.context.engine import ContextEngine
from src.context.models import BuiltContext, ConversationState
from src.engine import run_scheme
from src.llm import get_llm_client, get_llm_model
from src.query import hydrate_candidates, save_retrieval_json, sentence_pool_size
from src.query_agent.agent import QueryAgent
from src.query_agent.models import StructuredQuery
from src.query_agent.react_agent import AgentRetrievalResult, ReactQueryAgent
from src.store import load_config, resolve_path
from src.tag_retrieve import extract_query_tags, resolve_retrieval_config


class ContextService:
    def __init__(
        self,
        *,
        conversation: ConversationManager | None = None,
        context_engine: ContextEngine | None = None,
        query_agent: QueryAgent | None = None,
        react_agent: ReactQueryAgent | None = None,
    ):
        self.conversation = conversation or ConversationManager()
        self.context_engine = context_engine or ContextEngine(
            conversation=self.conversation
        )
        self.query_agent = query_agent or QueryAgent(
            context_engine=self.context_engine
        )
        self.react_agent = react_agent or ReactQueryAgent(
            context_engine=self.context_engine
        )

    def _query_mode(self) -> str:
        cfg = load_config().get("query_agent") or {}
        return str(cfg.get("mode") or "react").strip().lower()

    def _retrieve(
        self,
        query: str,
        *,
        structured: StructuredQuery | None = None,
        use_vector: bool = True,
        plan_names: list[str] | None = None,
        scheme: str | None = None,
    ) -> tuple[list[dict], list[str], dict]:
        cfg = resolve_retrieval_config()
        pool = sentence_pool_size(cfg.top_k)
        if not use_vector and scheme is None and plan_names is None:
            scheme = "tag_only"
        if plan_names is not None and scheme is None:
            from src.engine.schemes import RetrievalScheme, run_scheme as _run

            sch = RetrievalScheme(
                id=",".join(plan_names),
                label=",".join(plan_names),
                operators=plan_names,
                merge="max",
            )
            candidates, used = _run(
                query, sch, structured=structured, top_k=pool
            )
        else:
            candidates, used = run_scheme(
                query, scheme, structured=structured, top_k=pool
            )

        chunks = hydrate_candidates(candidates, top_k=cfg.top_k)
        plan = list(used.operators)
        meta = used.to_public()
        return chunks, plan, meta

    def _run_query_agent(
        self,
        query: str,
        state: ConversationState,
    ) -> StructuredQuery:
        return self.query_agent.process(query, state=state)

    def handle_turn(
        self,
        query: str,
        *,
        conversation_id: str | None = None,
        use_vector: bool = True,
        plan_names: list[str] | None = None,
        scheme: str | None = None,
        persist: bool = True,
        date_from: str | None = None,
        date_to: str | None = None,
        dates: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        完整一轮：
        1) 确保 conversation
        2) 会话短期记忆：溢出滑动窗口 → 更新 summary
        3) Query Agent → Structured Query（并挂上本轮前端日期集合/区间）
        4) 按需 Memory Engine 召回（仅在选定日期内，若指定）
        5) Context Builder：System + Summary + Recent(窗口) + (本轮∪窗口内曾召回) Memories + Query
        6) LLM 回答
        7) 写入 user/assistant，并持久化本轮 retrieval_trace（供后续轮次回灌）
        """
        from src.engine.date_range import normalize_date_list

        cid = self.conversation.get_or_create(conversation_id)
        state = self.conversation.load(cid)

        # 先维护会话短期记忆：窗口外旧对话 → summary，再参与本轮构图
        self.context_engine.ensure_summary(state)

        mode = self._query_mode()
        scheme_meta: dict = {}
        tool_trace: list = []
        timeline: list = []
        analysis: dict = {}
        dset = normalize_date_list(dates)

        if mode == "react" and (load_config().get("query_agent") or {}).get(
            "enabled", True
        ):
            # ReAct 中枢：分析 → grep(chunk原文)/rag → 合并证据
            result: AgentRetrievalResult = self.react_agent.retrieve(
                query,
                state=state,
                date_from=date_from,
                date_to=date_to,
                dates=dset,
                scheme=scheme,
            )
            structured = result.structured
            structured.dates = dset
            if not dset:
                structured.date_from = (date_from or "").strip()
                structured.date_to = (date_to or "").strip()
            else:
                structured.date_from = ""
                structured.date_to = ""
            chunks = result.chunks
            plan = ["react"]
            scheme_meta = {
                "id": "react",
                "stop_reason": result.stop_reason,
                "timeline": result.timeline,
            }
            tool_trace = result.tool_trace
            timeline = result.timeline
            analysis = result.analysis
            if not structured.need_retrieval:
                chunks = []
        else:
            structured = self._run_query_agent(query, state)
            structured.dates = dset
            if not dset:
                structured.date_from = (date_from or "").strip()
                structured.date_to = (date_to or "").strip()
            else:
                structured.date_from = ""
                structured.date_to = ""
            if structured.need_retrieval:
                chunks, plan, scheme_meta = self._retrieve(
                    structured.retrieval_query(),
                    structured=structured,
                    use_vector=use_vector,
                    plan_names=None
                    if scheme
                    else (plan_names or structured.retrieval_plan or None),
                    scheme=scheme,
                )
            else:
                chunks, plan = [], []

        qside = extract_query_tags(structured.retrieval_query())
        start, end = structured.date_range()
        retrieval_payload = {
            "type": "retrieval" if structured.need_retrieval else "skipped",
            "mode": mode,
            "count": len(chunks),
            "chunks": chunks,
            "plan": plan,
            "scheme": scheme_meta,
            "dates": structured.allowed_dates(),
            "date_from": start or "",
            "date_to": end or "",
            "structured_query": structured.to_dict(),
            "timeline": timeline,
            "tool_trace": tool_trace,
            "analysis": analysis,
            "query_tags": {
                "entities": qside.entities,
                "keywords": qside.keywords,
            },
        }
        save_retrieval_json(retrieval_payload, query)

        trace = {
            "plan": plan,
            "scheme": scheme_meta,
            "structured_query": structured.to_dict(),
            "themes": structured.query_themes,
            "dates": structured.allowed_dates(),
            "date_from": start or "",
            "date_to": end or "",
            "timeline": timeline,
            "tool_trace": tool_trace,
            "candidate_ids": [c["id"] for c in chunks],
            "scores": {c["id"]: c.get("score") for c in chunks},
        }

        # Context Builder：窗口对话 + 摘要 + 本轮/历史召回 chunk + 当前问题
        built = self.context_engine.build_context(
            query=query,
            state=state,
            memories=chunks,
            retrieval_trace=trace,
        )

        answer = self._call_llm(built)
        user_mid = None
        if persist:
            user_mid = self.conversation.append_message(cid, "user", query)
            self.conversation.append_message(cid, "assistant", answer)
            self.conversation.save_retrieval_trace(
                cid,
                user_message_id=user_mid,
                query=query,
                plan=plan,
                candidates=[
                    {
                        "chunk_id": c.get("chunk_id") or c["id"],
                        "score": c.get("score"),
                        "source": c.get("source"),
                        "date": c.get("date"),
                        "text": c.get("text") or "",
                        "matched_sentences": c.get("matched_sentences") or [],
                    }
                    for c in chunks
                ],
            )

        self._save_context_debug(built, query, cid)

        return {
            "conversation_id": cid,
            "answer": answer,
            "structured_query": structured.to_dict(),
            "plan": plan,
            "scheme": scheme_meta,
            "memories_used": [
                {
                    "unit_id": m.unit_id,
                    "chunk_id": m.chunk_id,
                    "date": m.date,
                    "score": m.score,
                    "source": m.source,
                    "recall_origin": m.recall_origin,
                    "matched_sentences": [
                        {
                            "id": h.get("id"),
                            "score": h.get("score"),
                            "text": h.get("text"),
                        }
                        for h in (m.matched_sentences or [])
                    ],
                }
                for m in built.memories
            ],
            "token_estimate": built.token_estimate,
            "budget_used": built.budget_used,
            "retrieval": retrieval_payload,
            "context_messages_preview": [
                {"role": m["role"], "chars": len(m["content"])} for m in built.messages
            ],
        }

    def _call_llm(self, built: BuiltContext) -> str:
        client = get_llm_client("answer")
        try:
            response = client.chat.completions.create(
                model=get_llm_model("answer"),
                messages=built.messages,
                temperature=0.3,
            )
            text = response.choices[0].message.content
            if text and text.strip():
                return text.strip()
        except Exception as exc:
            print(f"  [warn] LLM 调用失败，降级: {exc}")

        if not built.memories:
            return "（暂时没有检索到相关日记，也可以继续聊聊。）"
        lines = [
            f"[{m.date}] {m.text[:120]}…" if len(m.text) > 120 else f"[{m.date}] {m.text}"
            for m in built.memories[:5]
        ]
        return "相关记忆片段：\n" + "\n".join(lines)

    def _save_context_debug(
        self, built: BuiltContext, query: str, conversation_id: str
    ) -> None:
        cfg = load_config().get("context") or {}
        if not bool(cfg.get("save_debug_json", True)):
            return
        out = resolve_path(cfg.get("debug_path", "data/last_context.json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id,
            "query": query,
            "token_estimate": built.token_estimate,
            "budget_used": built.budget_used,
            "summary": built.summary,
            "memories": [asdict(m) for m in built.memories],
            "recent": [asdict(m) for m in built.recent],
            "retrieval_trace": built.retrieval_trace,
            "messages": [
                {"role": m["role"], "content": m["content"][:2000]}
                for m in built.messages
            ],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
