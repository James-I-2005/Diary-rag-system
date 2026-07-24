"""Memory Extraction 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryViewDraft:
    type: str
    content: str


@dataclass
class ExtractionResult:
    chunk_id: str
    views: list[MemoryViewDraft] = field(default_factory=list)
    raw: str = ""

    def to_view_dicts(self) -> list[dict[str, str]]:
        return [{"type": v.type, "content": v.content} for v in self.views]
