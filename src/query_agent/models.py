"""Query Agent 数据模型：改写 + query rag-sentences。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StructuredQuery:
    """Query Agent 输出：改写结果 + 查询侧 rag-sentences。"""

    original_query: str
    rewritten_query: str
    query_sentences: list[str] = field(default_factory=list)
    need_retrieval: bool = True  # 空输入除外；本 Agent 不做意图路由
    intent: str = "unknown"  # 兼容旧字段，固定 unknown
    retrieval_plan: list[str] = field(default_factory=list)
    embedding_query: str = ""
    source: str = "llm"
    meta: dict[str, Any] = field(default_factory=dict)

    def retrieval_query(self) -> str:
        """供 Tag 等使用：优先改写句。"""
        text = (self.rewritten_query or self.original_query).strip()
        return text or self.original_query

    def view_retrieval_query(self) -> str:
        """供 EmbeddingOperator：优先拼接 query rag-sentences。"""
        if self.query_sentences:
            return "\n".join(s.strip() for s in self.query_sentences if s.strip())
        text = (self.embedding_query or self.rewritten_query or self.original_query).strip()
        return text or self.original_query

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
