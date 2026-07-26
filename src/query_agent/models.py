"""Query Agent 数据模型：主题改写 + 多路 embedding 检索。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StructuredQuery:
    """Query Agent 输出：1~3 个检索主题短语。"""

    original_query: str
    rewritten_query: str
    # 主题短语（与 query_rewrite.md 对齐）；兼容旧字段名 query_sentences
    query_sentences: list[str] = field(default_factory=list)
    need_retrieval: bool = True
    intent: str = "unknown"
    retrieval_plan: list[str] = field(default_factory=list)
    embedding_query: str = ""
    source: str = "llm"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def query_themes(self) -> list[str]:
        """检索主题列表（1~3）。"""
        return [s.strip() for s in self.query_sentences if str(s).strip()]

    def retrieval_query(self) -> str:
        """供 Tag 等单路使用：主题拼接或改写句。"""
        themes = self.query_themes
        if themes:
            return "；".join(themes)
        text = (self.rewritten_query or self.original_query).strip()
        return text or self.original_query

    def view_retrieval_query(self) -> str:
        """兼容旧调用：多主题时返回拼接文本；真正多路检索由 EmbeddingOperator 读 query_themes。"""
        themes = self.query_themes
        if themes:
            return "\n".join(themes)
        text = (
            self.embedding_query or self.rewritten_query or self.original_query
        ).strip()
        return text or self.original_query

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["query_themes"] = self.query_themes
        return d
