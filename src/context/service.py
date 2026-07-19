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
from src.engine import PlanExecutor, build_plan, build_plan_from_config
from src.llm import get_llm_client, get_llm_model
from src.query import hydrate_candidates, save_retrieval_json
from src.store import load_config, resolve_path
from src.tag_retrieve import extract_query_tags, resolve_retrieval_config


class ContextService:
    def __init__(
        self,
        *,
        conversation: ConversationManager | None = None,
        context_engine: ContextEngine | None = None,
    ):
        self.conversation = conversation or ConversationManager()
        self.context_engine = context_engine or ContextEngine(
            conversation=self.conversation
        )

    def _retrieve(
        self,
        query: str,
        *,
        use_vector: bool = True,
        plan_names: list[str] | None = None,
    ) -> tuple[list[dict], list[str]]:
        cfg = resolve_retrieval_config()
        if plan_names is not None:
            names = plan_names
        elif not use_vector:
            names = ["tag"]
        else:
            names = None
        plan = (
            build_plan(names, top_k=cfg.top_k)
            if names is not None
            else build_plan_from_config(top_k=cfg.top_k)
        )
        candidates = PlanExecutor().run(query, plan)
        chunks = hydrate_candidates(candidates, top_k=cfg.top_k)
        return chunks, [op.name for op in plan.operators]

    def handle_turn(
        self,
        query: str,
        *,
        conversation_id: str | None = None,
        use_vector: bool = True,
        plan_names: list[str] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """
        完整一轮：
        1) 确保 conversation
        2) Memory Engine 召回（临时）
        3) ContextEngine 构图
        4) LLM 回答
        5) 仅把 user/assistant 消息写入 Conversation（不含 memories）
        """
        cid = self.conversation.get_or_create(conversation_id)
        state = self.conversation.load(cid)

        chunks, plan = self._retrieve(
            query, use_vector=use_vector, plan_names=plan_names
        )
        qside = extract_query_tags(query)
        retrieval_payload = {
            "type": "retrieval",
            "count": len(chunks),
            "chunks": chunks,
            "plan": plan,
            "query_tags": {
                "entities": qside.entities,
                "keywords": qside.keywords,
            },
        }
        save_retrieval_json(retrieval_payload, query)

        trace = {
            "plan": plan,
            "candidate_ids": [c["id"] for c in chunks],
            "scores": {c["id"]: c.get("score") for c in chunks},
        }

        built = self.context_engine.build(
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
                        "chunk_id": c["id"],
                        "score": c.get("score"),
                        "source": c.get("source"),
                        "date": c.get("date"),
                    }
                    for c in chunks
                ],
            )
            # 刷新 summary（规则）
            state2 = self.conversation.load(cid)
            self.context_engine.maybe_update_summary(state2)

        self._save_context_debug(built, query, cid)

        return {
            "conversation_id": cid,
            "answer": answer,
            "plan": plan,
            "memories_used": [
                {
                    "chunk_id": m.chunk_id,
                    "date": m.date,
                    "score": m.score,
                    "source": m.source,
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
            return "未找到相关日记内容，请尝试换个问法。"
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
