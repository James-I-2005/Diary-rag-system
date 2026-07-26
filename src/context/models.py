"""Context 层数据模型（与 Retrieval Engine 的 Candidate 对齐）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from src.engine.candidate import Candidate

Role = Literal["system", "user", "assistant"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Message:
    role: Role
    content: str
    timestamp: str = field(default_factory=_now_iso)
    id: str | None = None


@dataclass
class RetrievedMemory:
    """进入 Context 的临时记忆；召回单位=chunk，匹配理由=命中的 rag-sentences。"""

    unit_id: str
    score: float = 0.0
    source: str = ""
    date: str = ""
    text: str = ""  # chunk 全文
    chunk_id: str = ""
    evidence_text: str = ""  # 兼容旧字段；与 text 同为 chunk 全文
    matched_sentences: list[dict[str, Any]] = field(default_factory=list)
    # current=本轮检索；prior=会话窗口内更早轮次曾召回
    recall_origin: str = "current"

    @classmethod
    def from_candidate(
        cls,
        c: Candidate,
        *,
        date: str = "",
        text: str = "",
    ) -> RetrievedMemory:
        return cls(
            unit_id=c.unit_id,
            score=float(c.score),
            source=c.source or "",
            date=date,
            text=text,
            chunk_id=c.chunk_id,
        )

    @classmethod
    def from_hydrated(cls, row: dict[str, Any]) -> RetrievedMemory:
        text = str(row.get("text") or "")
        evidence = str(row.get("evidence_text") or "")
        unit = str(row.get("id") or row.get("unit_id") or "")
        parent = str(row.get("chunk_id") or "")
        hits = row.get("matched_sentences") or []
        if not isinstance(hits, list):
            hits = []
        origin = str(row.get("recall_origin") or "current").strip() or "current"
        return cls(
            unit_id=unit,
            score=float(row.get("score") or 0.0),
            source=str(row.get("source") or ""),
            date=str(row.get("date") or ""),
            text=text,
            chunk_id=parent or unit,
            evidence_text=evidence or text,
            matched_sentences=[h for h in hits if isinstance(h, dict)],
            recall_origin=origin,
        )


@dataclass
class ConversationState:
    conversation_id: str
    summary: str = ""
    messages: list[Message] = field(default_factory=list)
    summary_upto: int = 0


@dataclass
class BuiltContext:
    messages: list[dict[str, str]]
    system: str = ""
    summary: str = ""
    recent: list[Message] = field(default_factory=list)
    memories: list[RetrievedMemory] = field(default_factory=list)
    query: str = ""
    token_estimate: int = 0
    budget_used: dict[str, int] = field(default_factory=dict)
    retrieval_trace: dict[str, Any] = field(default_factory=dict)
