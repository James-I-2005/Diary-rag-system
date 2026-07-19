"""Pipeline 中流动的轻量 Candidate。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Candidate:
    chunk_id: str
    score: float = 0.0
    source: str = ""  # 调试用：tag / embedding / tag+embedding


def merge_candidates(
    left: list[Candidate],
    right: list[Candidate],
) -> list[Candidate]:
    """按 chunk_id 并集合并；同分取 max；source 用 + 拼接去重。"""
    by_id: dict[str, Candidate] = {}
    for c in [*left, *right]:
        existing = by_id.get(c.chunk_id)
        if existing is None:
            by_id[c.chunk_id] = Candidate(
                chunk_id=c.chunk_id,
                score=float(c.score),
                source=c.source or "",
            )
            continue
        if float(c.score) > existing.score:
            existing.score = float(c.score)
        if c.source and c.source not in existing.source.split("+"):
            parts = [p for p in existing.source.split("+") if p]
            if c.source not in parts:
                parts.append(c.source)
            existing.source = "+".join(parts) if parts else c.source
    return sorted(by_id.values(), key=lambda x: (-x.score, x.chunk_id))
