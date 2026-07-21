"""Query Agent：理解意图、重写查询、输出 StructuredQuery。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from src.context.models import ConversationState
from src.engine.registry import resolve_plan_names
from src.llm import get_llm_client, get_llm_model
from src.query_agent.models import VALID_INTENTS, Intent, StructuredQuery
from src.store import load_config, resolve_path

_GREETING_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|早上好|下午好|晚上好|在吗|在不在|"
    r"谢谢|多谢|感谢|好的|ok|okay|嗯|嗯嗯|哈哈+|呵呵+|"
    r"你是谁|你叫什么|介绍一下自己)[\s!！?？。~～]*$",
    re.IGNORECASE,
)

_UNDERSTAND_PROMPT = """你是 Query Agent。不回答用户，只做查询理解，并严格输出一个 JSON 对象（不要 markdown）。

字段：
- need_retrieval: bool  是否需要检索个人日记
- intent: string        见下方枚举
- rewritten_query: string  始终填写；供检索/下游使用的自包含查询

intent 枚举与互斥（按顺序判定，命中即停）：
1. conversation — 寒暄、感谢、闲聊、问助手身份；与日记内容无关
2. summary — 要求归纳/统计/偏好/一段时间概况（「总结一下」「这段时间怎么样」「我最常…」）
3. memory_search — 查有无记录、是否写过某主题/关键词（「有没有写过」「日记里提过吗」）
4. memory_recall — 回忆具体经历、人物、事件、时间点（「…发生了什么」「谁/何时/何地」）
5. unknown — 无法归入以上，但可能与日记有关

need_retrieval 约束：
- conversation → false
- summary / memory_search / memory_recall → true
- unknown → true（宁可检索）
- 不确定是否需检索 → true

rewritten_query 规则：
- conversation：可与用户原文相同
- 需要检索时：写成可独立理解的检索问句；结合对话上下文消解「后来呢/那次/他」等指代
- 只补全指代与必要时间/人物线索，不编造日记里没有的事实
- 不要回答问题，不要复制整段对话，不要加「请检索」等元指令
- 无指代且表达已清晰时，可轻微整理标点/口语，保留原意

若提供了「对话摘要/最近对话」，仅用于消歧与指代消解；摘要与当前句冲突时以当前用户输入为准。

示例：
用户：你好
→ {"need_retrieval":false,"intent":"conversation","rewritten_query":"你好"}

用户：我有没有写过关于焦虑的内容？
→ {"need_retrieval":true,"intent":"memory_search","rewritten_query":"日记中是否写过与焦虑相关的内容"}

用户：去年夏天发生了什么？
→ {"need_retrieval":true,"intent":"summary","rewritten_query":"归纳去年夏天日记中的主要经历与事件"}

用户：那次东京旅行怎么样？（上下文刚谈过2019东京行）
→ {"need_retrieval":true,"intent":"memory_recall","rewritten_query":"2019年东京旅行的经历与感受"}

只输出 JSON：
{"need_retrieval":true,"intent":"memory_recall","rewritten_query":"..."}"""


class QueryAgent:
    """Memory Runtime 入口：Raw Query → Structured Query。"""

    def __init__(self) -> None:
        self._cfg = self._load_cfg()

    def _load_cfg(self) -> dict[str, Any]:
        return load_config().get("query_agent") or {}

    def enabled(self) -> bool:
        env = os.getenv("QUERY_AGENT_ENABLED", "").strip().lower()
        if env:
            return env in {"1", "true", "yes", "on"}
        return bool(self._cfg.get("enabled", True))

    def llm_role(self) -> str:
        return (
            os.getenv("QUERY_AGENT_LLM_ROLE", "").strip()
            or str(self._cfg.get("llm_role") or "tags")
        )

    def rule_fast_path(self) -> bool:
        env = os.getenv("QUERY_AGENT_RULE_FAST_PATH", "").strip().lower()
        if env:
            return env in {"1", "true", "yes", "on"}
        return bool(self._cfg.get("rule_fast_path", True))

    def default_plan(self) -> list[str]:
        try:
            from src.engine.schemes import get_scheme

            return list(get_scheme().operators)
        except Exception:
            return list(resolve_plan_names())

    def process(
        self,
        raw_query: str,
        *,
        state: ConversationState | None = None,
    ) -> StructuredQuery:
        original = raw_query.strip()
        if not original:
            return StructuredQuery(
                original_query=raw_query,
                rewritten_query=raw_query,
                need_retrieval=False,
                intent="conversation",
                retrieval_plan=[],
                source="rule",
            )

        if not self.enabled():
            return self._passthrough(original)

        if self.rule_fast_path():
            ruled = self._try_rule(original)
            if ruled is not None:
                self._save_debug(ruled, state)
                return ruled

        try:
            structured = self._llm_process(original, state=state)
        except Exception as exc:
            print(f"  [warn] Query Agent LLM 失败，降级 recall 优先: {exc}")
            structured = StructuredQuery(
                original_query=original,
                rewritten_query=original,
                need_retrieval=True,
                intent="unknown",
                retrieval_plan=self.default_plan(),
                source="fallback",
                meta={"error": str(exc)},
            )

        self._save_debug(structured, state)
        return structured

    def _passthrough(self, original: str) -> StructuredQuery:
        return StructuredQuery(
            original_query=original,
            rewritten_query=original,
            need_retrieval=True,
            intent="unknown",
            retrieval_plan=self.default_plan(),
            source="disabled",
        )

    def _try_rule(self, text: str) -> StructuredQuery | None:
        t = text.strip()
        if len(t) <= 16 and _GREETING_RE.match(t):
            return StructuredQuery(
                original_query=text,
                rewritten_query=text,
                need_retrieval=False,
                intent="conversation",
                retrieval_plan=[],
                source="rule",
            )
        return None

    def _format_context(self, state: ConversationState | None) -> str:
        if not state:
            return ""
        parts: list[str] = []
        if state.summary.strip():
            parts.append(f"对话摘要：{state.summary.strip()}")
        recent = state.messages[-6:]
        if recent:
            lines = [f"{m.role}: {m.content}" for m in recent]
            parts.append("最近对话：\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def _llm_process(
        self,
        original: str,
        *,
        state: ConversationState | None,
    ) -> StructuredQuery:
        context = self._format_context(state)
        user_parts = [f"用户输入：{original}"]
        if context:
            user_parts.append(context)
        user_content = "\n\n".join(user_parts)

        client = get_llm_client(self.llm_role())
        resp = client.chat.completions.create(
            model=get_llm_model(self.llm_role()),
            messages=[
                {"role": "system", "content": _UNDERSTAND_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = self._parse_json(raw)

        need_retrieval = bool(data.get("need_retrieval", True))
        intent = self._normalize_intent(data.get("intent"))
        rewritten = str(data.get("rewritten_query") or original).strip() or original

        # 与 prompt 约束双保险：intent 决定 need_retrieval
        if intent == "conversation":
            need_retrieval = False
        elif intent in {"summary", "memory_search", "memory_recall", "unknown"}:
            need_retrieval = True

        plan = self.default_plan() if need_retrieval else []

        return StructuredQuery(
            original_query=original,
            rewritten_query=rewritten,
            need_retrieval=need_retrieval,
            intent=intent,
            retrieval_plan=plan,
            source="llm",
            meta={"llm_raw": raw[:500]},
        )

    def _parse_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
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
            data = json.loads(m.group())
            if isinstance(data, dict):
                return data
        raise ValueError(f"无法解析 Query Agent JSON: {text[:200]}")

    def _normalize_intent(self, raw: Any) -> Intent:
        key = str(raw or "unknown").strip().lower().replace("-", "_")
        if key in VALID_INTENTS:
            return key  # type: ignore[return-value]
        aliases = {
            "chat": "conversation",
            "greeting": "conversation",
            "recall": "memory_recall",
            "search": "memory_search",
            "summarize": "summary",
            "summarization": "summary",
        }
        return aliases.get(key, "unknown")  # type: ignore[return-value]

    def _save_debug(
        self,
        structured: StructuredQuery,
        state: ConversationState | None,
    ) -> None:
        if not bool(self._cfg.get("save_debug_json", True)):
            return
        out = resolve_path(self._cfg.get("debug_path", "data/last_query.json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": state.conversation_id if state else None,
            **structured.to_dict(),
        }
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
