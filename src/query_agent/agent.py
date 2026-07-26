"""Query Agent：用 query_rewrite.md 把用户问题改写成 1~3 个检索主题。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.context.models import ConversationState
from src.llm import get_llm_client, get_llm_model
from src.query_agent.models import StructuredQuery
from src.store import load_config, resolve_path

_PROMPT_PATH = Path(__file__).with_name("query_rewrite.md")

_FALLBACK_PROMPT = """你是一个用于向量检索（Embedding Search）的 Query 重写助手。

用户的输入通常带有模糊回忆或提问语气（例如：“我记得我看过某某视频学到了什么”）。
请将其改写为 1~3 个【客观、具体的主题短语】，去除所有“我记得”、“学到了什么”、“曾经看过”等无意义的修饰词。

改写要求：
1. 提取或推导文本核心涉及的客观主题、知识领域或实体。
2. 保持短语干净、客观，直接对应知识库中可能存在的内容主题。
3. 数量控制在 1~3 个，按相关度输出。

示例输入：我记得我曾经看过一个和非洲相关的视频 当时我学到了什么
示例输出：
- 非洲历史与地理文化知识
- 非洲社会经济与自然风貌科普
"""


def load_query_rewrite_prompt() -> str:
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _FALLBACK_PROMPT.strip()


class QueryAgent:
    """Raw Query → 1~3 检索主题短语。"""

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

    def max_themes(self) -> int:
        raw = os.getenv("QUERY_AGENT_MAX_THEMES", "").strip()
        if raw:
            return max(1, min(5, int(raw)))
        return max(1, min(5, int(self._cfg.get("max_themes", 3))))

    def default_plan(self) -> list[str]:
        """主题多路 embedding 为主；可用 config scheme 覆盖。"""
        try:
            from src.engine.schemes import get_scheme

            return list(get_scheme().operators)
        except Exception:
            return ["embedding"]

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
        if not state:
            return ""
        parts: list[str] = []
        if state.summary.strip():
            parts.append(f"对话摘要：{state.summary.strip()}")
        n = self._session_window_turns() * 2
        recent = state.messages[-n:] if n > 0 else []
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
        user_parts.append(
            "请只输出 1~3 条主题短语，每行一条，可用 `- ` 开头；不要解释。"
        )
        user_content = "\n\n".join(user_parts)

        client = get_llm_client(self.llm_role())
        resp = client.chat.completions.create(
            model=get_llm_model(self.llm_role()),
            messages=[
                {"role": "system", "content": load_query_rewrite_prompt()},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        themes = self._parse_themes(raw)
        if not themes:
            themes = [original]

        rewritten = "；".join(themes)
        return StructuredQuery(
            original_query=original,
            rewritten_query=rewritten,
            query_sentences=themes,
            need_retrieval=True,
            retrieval_plan=self.default_plan(),
            embedding_query="\n".join(themes),
            source="llm",
            meta={"llm_raw": raw[:800], "n_themes": len(themes)},
        )

    def _parse_themes(self, text: str) -> list[str]:
        """解析 bullet / 编号 / JSON / 纯换行主题列表。"""
        text = (text or "").strip()
        if not text:
            return []

        # 尝试 JSON：{"themes":[...]} / {"query_themes":[...]} / list
        try:
            blob = text
            if blob.startswith("```"):
                blob = re.sub(r"^```(?:json)?\s*", "", blob)
                blob = re.sub(r"\s*```$", "", blob)
            data = json.loads(blob)
            if isinstance(data, list):
                return self._normalize_theme_list(data)
            if isinstance(data, dict):
                for key in ("themes", "query_themes", "query_sentences", "topics"):
                    if key in data:
                        return self._normalize_theme_list(data.get(key))
                if "rewritten_query" in data and not any(
                    k in data for k in ("themes", "query_themes", "query_sentences")
                ):
                    # 旧格式兜底
                    sents = data.get("query_sentences")
                    if sents:
                        return self._normalize_theme_list(sents)
                    rq = str(data.get("rewritten_query") or "").strip()
                    return [rq] if rq else []
        except (json.JSONDecodeError, TypeError):
            pass

        out: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("```"):
                continue
            # "- xxx" / "* xxx" / "• xxx" / "1. xxx" / "1) xxx"
            s = re.sub(r"^[-*•]\s+", "", s)
            s = re.sub(r"^\d+[.)、]\s*", "", s)
            s = s.strip().strip("「」\"'")
            if s and s not in out:
                out.append(s)
            if len(out) >= self.max_themes():
                break
        return out[: self.max_themes()]

    def _normalize_theme_list(self, raw: Any) -> list[str]:
        out: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                s = str(item or "").strip()
                if s and s not in out:
                    out.append(s)
        elif isinstance(raw, str) and raw.strip():
            return self._parse_themes(raw)
        return out[: self.max_themes()]

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
            "prompt_file": str(_PROMPT_PATH.name),
            **structured.to_dict(),
        }
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
