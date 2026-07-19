"""Context 层数据模型（与 Retrieval Engine 的 Candidate 对齐，不含日记正文存储）。"""

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
    """进入 Context 的临时记忆；不写入 Conversation History。"""

    chunk_id: str
    score: float = 0.0
    source: str = ""
    date: str = ""
    text: str = ""

    @classmethod
    def from_candidate(
        cls,
        c: Candidate,
        *,
        date: str = "",
        text: str = "",
    ) -> RetrievedMemory:
        return cls(
            chunk_id=c.chunk_id,
            score=float(c.score),
            source=c.source or "",
            date=date,
            text=text,
        )

    @classmethod
    def from_hydrated(cls, row: dict[str, Any]) -> RetrievedMemory:
        return cls(
            chunk_id=str(row.get("id") or row.get("chunk_id") or ""),
            score=float(row.get("score") or 0.0),
            source=str(row.get("source") or ""),
            date=str(row.get("date") or ""),
            text=str(row.get("text") or ""),
        )


@dataclass
class ConversationState:
    conversation_id: str
    summary: str = ""
    messages: list[Message] = field(default_factory=list)


@dataclass
class BuiltContext:
    """最终交给 LLM 的上下文（messages 格式 + 调试元信息）。"""

    messages: list[dict[str, str]]
    system: str = ""
    summary: str = ""
    recent: list[Message] = field(default_factory=list)
    memories: list[RetrievedMemory] = field(default_factory=list)
    query: str = ""
    token_estimate: int = 0
    budget_used: dict[str, int] = field(default_factory=dict)
    retrieval_trace: dict[str, Any] = field(default_factory=dict)
