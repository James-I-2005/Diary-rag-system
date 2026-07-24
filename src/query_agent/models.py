"""Query Agent 数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Intent = Literal[
    "conversation",
    "memory_recall",
    "memory_search",
    "summary",
    "unknown",
]

VALID_INTENTS: frozenset[str] = frozenset(
    {"conversation", "memory_recall", "memory_search", "summary", "unknown"}
)

VALID_VIEW_TYPE_HINTS: frozenset[str] = frozenset(
    {"event", "narrative", "growth", "identity", "future_query"}
)

INTENT_VIEW_HINTS: dict[str, list[str]] = {
    "memory_recall": ["event", "narrative"],
    "memory_search": ["event", "future_query"],
    "summary": ["growth", "identity", "event"],
}


@dataclass
class QueryRepresentation:
    semantic_facets: list[str] = field(default_factory=list)
    view_type_hints: list[str] = field(default_factory=list)
    time_range: dict[str, str] | None = None
    entity_hints: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> QueryRepresentation | None:
        if not raw or not isinstance(raw, dict):
            return None
        hints = [
            h
            for h in (raw.get("view_type_hints") or [])
            if str(h).strip().lower() in VALID_VIEW_TYPE_HINTS
        ]
        facets = [str(f).strip() for f in (raw.get("semantic_facets") or []) if str(f).strip()][:8]
        entities = [str(e).strip() for e in (raw.get("entity_hints") or []) if str(e).strip()]
        tr = raw.get("time_range")
        time_range = tr if isinstance(tr, dict) else None
        return cls(
            semantic_facets=facets,
            view_type_hints=hints,
            time_range=time_range,
            entity_hints=entities,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredQuery:
    """Query Agent 输出：下游 Memory Engine / Context Engine 统一消费。"""

    original_query: str
    rewritten_query: str
    need_retrieval: bool = True
    intent: Intent = "unknown"
    retrieval_plan: list[str] = field(default_factory=list)
    query_representation: QueryRepresentation | None = None
    embedding_query: str = ""
    source: str = "llm"  # llm | rule | fallback
    meta: dict[str, Any] = field(default_factory=dict)

    def retrieval_query(self) -> str:
        """供 TagOperator 使用的查询文本。"""
        text = (self.rewritten_query or self.original_query).strip()
        return text or self.original_query

    def view_retrieval_query(self) -> str:
        """供 ViewOperator 使用的 embedding 查询文本。"""
        text = (self.embedding_query or self.rewritten_query or self.original_query).strip()
        return text or self.original_query

    def effective_view_type_hints(self) -> list[str]:
        if self.query_representation and self.query_representation.view_type_hints:
            return list(self.query_representation.view_type_hints)
        return list(INTENT_VIEW_HINTS.get(self.intent, []))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.query_representation:
            d["query_representation"] = self.query_representation.to_dict()
        return d
