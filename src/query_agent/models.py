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


@dataclass
class StructuredQuery:
    """Query Agent 输出：下游 Memory Engine / Context Engine 统一消费。"""

    original_query: str
    rewritten_query: str
    need_retrieval: bool = True
    intent: Intent = "unknown"
    retrieval_plan: list[str] = field(default_factory=list)
    source: str = "llm"  # llm | rule | fallback
    meta: dict[str, Any] = field(default_factory=dict)

    def retrieval_query(self) -> str:
        """供 Memory Engine 使用的查询文本。"""
        text = (self.rewritten_query or self.original_query).strip()
        return text or self.original_query

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
