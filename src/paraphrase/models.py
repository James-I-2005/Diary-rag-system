"""Paraphrase 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParaphraseResult:
    chunk_id: str
    sentences: list[str] = field(default_factory=list)
    raw: str = ""
