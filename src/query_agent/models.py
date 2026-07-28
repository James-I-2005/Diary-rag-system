"""Query Agent 数据模型：主题改写 + 多路 embedding 检索。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.engine.date_range import normalize_date_list


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
    # 召回日期：优先 dates 集合；空则回退 date_from~date_to 闭区间。皆空=不限制。
    dates: list[str] = field(default_factory=list)
    date_from: str = ""
    date_to: str = ""

    @property
    def query_themes(self) -> list[str]:
        """检索主题列表（1~3）。"""
        return [s.strip() for s in self.query_sentences if str(s).strip()]

    def allowed_dates(self) -> list[str]:
        """规范化后的日期集合；空列表表示未用集合过滤。"""
        return normalize_date_list(self.dates)

    def date_range(self) -> tuple[str | None, str | None]:
        """归一化日期范围；若有 dates 则取 min/max；from>to 时自动对调。"""
        ds = self.allowed_dates()
        if ds:
            return ds[0], ds[-1]
        start = (self.date_from or "").strip() or None
        end = (self.date_to or "").strip() or None
        if start and end and start > end:
            start, end = end, start
        return start, end

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
        ds = self.allowed_dates()
        d["dates"] = ds
        start, end = self.date_range()
        d["date_from"] = start or ""
        d["date_to"] = end or ""
        return d
