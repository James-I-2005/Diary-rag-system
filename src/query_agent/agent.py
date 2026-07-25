"""Query Agent：改写用户问题，并拆成 query rag-sentences。除此之外不做路由/意图判断。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from src.context.models import ConversationState
from src.engine.registry import resolve_plan_names
from src.llm import get_llm_client, get_llm_model
from src.query_agent.models import StructuredQuery
from src.store import load_config, resolve_path

_QUERY_PROMPT = """你是 Query Agent。不回答用户，只处理用户问题，并严格输出一个 JSON 对象（不要 markdown）。

你只做两件事：

## 1. rewritten_query（改写）

把用户原话改写成更易被检索与模型理解的表述：
- 抽出隐含意思，补全指代（结合对话上下文中的「后来呢/那次/他」等）
- 删除修辞、口语填充、重复、无意义过渡语
- 不编造用户没说的事实；不要回答问题本身

## 2. query_sentences（Query RAG-Sentence）

参考 RAG-Sentence 规范，把改写后的查询意图拆成若干条检索友好的句子：
1. 每句表达一个相对独立、完整的语义单元
2. 脱离上下文也能理解；主语明确，代词还原为具体对象
3. 保留人物、对象、事件、观点、原因、感受、结论等检索线索
4. 删除修辞与填充；保持自然语言，不要列表/编号/JSON
5. 互不相关的语义拆开；共同表达一个完整意思的可合并
6. 不补充原文没有的信息
7. 合起来应覆盖改写后查询的主要检索意图

## 输出 JSON（仅这两个业务字段）

{
  "rewritten_query": "……",
  "query_sentences": [
    "……",
    "……"
  ]
}

示例：
用户：那次打羽毛球被小胖墩夸了，心里还挺美的，后来是不是也没啥期待结果反而还行？
→
{
  "rewritten_query": "回忆某次羽毛球被卷发小胖墩夸奖以及当天期待落空却感觉不错的经历",
  "query_sentences": [
    "用户询问某次羽毛球运动中被卷发小胖墩夸奖的经历。",
    "用户询问当天原本没有期待但实际感觉不错的经历。"
  ]
}

只输出 JSON，不要其它文字。"""


class QueryAgent:
    """Raw Query → 改写 + query rag-sentences。"""

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
                query_sentences=[],
                need_retrieval=False,
                retrieval_plan=[],
                source="rule",
            )

        if not self.enabled():
            return self._passthrough(original)

        try:
            structured = self._llm_process(original, state=state)
        except Exception as exc:
            print(f"  [warn] Query Agent LLM 失败，降级原文: {exc}")
            structured = StructuredQuery(
                original_query=original,
                rewritten_query=original,
                query_sentences=[original],
                need_retrieval=True,
                retrieval_plan=self.default_plan(),
                embedding_query=original,
                source="fallback",
                meta={"error": str(exc)},
            )

        self._save_debug(structured, state)
        return structured

    def _passthrough(self, original: str) -> StructuredQuery:
        return StructuredQuery(
            original_query=original,
            rewritten_query=original,
            query_sentences=[original],
            need_retrieval=True,
            retrieval_plan=self.default_plan(),
            embedding_query=original,
            source="disabled",
        )

    def _session_window_turns(self) -> int:
        ctx = load_config().get("context") or {}
        if "session_window_turns" in ctx:
            return max(1, int(ctx["session_window_turns"]))
        return max(1, int(ctx.get("recent_message_turns", 10)))

    def _format_context(self, state: ConversationState | None) -> str:
        """与 Context Builder 一致：摘要 + 滑动窗口内最近对话。"""
        if not state:
            return ""
        parts: list[str] = []
        if state.summary.strip():
            parts.append(f"对话摘要：{state.summary.strip()}")
        n = self._session_window_turns() * 2
        recent = state.messages[-n:] if n > 0 else []
        # Query Agent 只需少量近期原文即可消解指代，最多取窗口内末 6 条
        recent = recent[-6:]
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
                {"role": "system", "content": _QUERY_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = self._parse_json(raw)

        rewritten = str(data.get("rewritten_query") or original).strip() or original
        sentences = self._normalize_sentences(
            data.get("query_sentences"), fallback=rewritten
        )
        embedding_query = "\n".join(sentences)

        return StructuredQuery(
            original_query=original,
            rewritten_query=rewritten,
            query_sentences=sentences,
            need_retrieval=True,
            retrieval_plan=self.default_plan(),
            embedding_query=embedding_query,
            source="llm",
            meta={"llm_raw": raw[:800]},
        )

    def _normalize_sentences(self, raw: Any, *, fallback: str) -> list[str]:
        out: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                s = str(item or "").strip()
                if s:
                    out.append(s)
        elif isinstance(raw, str) and raw.strip():
            for line in raw.splitlines():
                s = line.strip()
                if s:
                    out.append(s)
        if not out:
            out = [fallback.strip()] if fallback.strip() else []
        return out[:20]

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
