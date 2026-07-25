"""Pipeline 中流动的轻量 Candidate（v0.4：unit_id = rag-sentence id）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    unit_id: str
    score: float = 0.0
    source: str = ""  # tag / embedding / tag+embedding
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        """兼容旧调用：优先 meta.parent_chunk_id，否则 unit_id。"""
        parent = (self.meta or {}).get("parent_chunk_id")
        if parent:
            return str(parent)
        return self.unit_id


def merge_candidates(
    left: list[Candidate],
    right: list[Candidate],
) -> list[Candidate]:
    """按 unit_id 并集合并；同分取 max；source 用 + 拼接去重。"""
    by_id: dict[str, Candidate] = {}
    for c in [*left, *right]:
        existing = by_id.get(c.unit_id)
        if existing is None:
            by_id[c.unit_id] = Candidate(
                unit_id=c.unit_id,
                score=float(c.score),
                source=c.source or "",
                meta=dict(c.meta) if c.meta else {},
            )
            continue
        if float(c.score) > existing.score:
            existing.score = float(c.score)
        if c.source and c.source not in existing.source.split("+"):
            parts = [p for p in existing.source.split("+") if p]
            if c.source not in parts:
                parts.append(c.source)
            existing.source = "+".join(parts) if parts else c.source
        if c.meta:
            existing.meta.update(c.meta)
    return sorted(by_id.values(), key=lambda x: (-x.score, x.unit_id))


def _minmax_norm(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi <= lo:
        return [1.0] * len(scores)
    span = hi - lo
    return [(s - lo) / span for s in scores]


def merge_candidates_weighted_paths(
    paths: dict[str, list[Candidate]],
    weights: dict[str, float],
    *,
    top_k: int | None = None,
) -> list[Candidate]:
    """多路归一化加权合并。"""
    norm_maps: dict[str, dict[str, float]] = {}
    for name, hits in paths.items():
        norm_maps[name] = {
            c.unit_id: s
            for c, s in zip(hits, _minmax_norm([float(c.score) for c in hits]))
        }

    ids: set[str] = set()
    for m in norm_maps.values():
        ids |= set(m.keys())

    out: list[Candidate] = []
    for uid in ids:
        score = 0.0
        sources: list[str] = []
        meta: dict = {}
        for name, w in weights.items():
            if uid in norm_maps.get(name, {}):
                score += w * norm_maps[name][uid]
                sources.append(name)
                for c in paths.get(name, []):
                    if c.unit_id == uid and c.meta:
                        meta.update(c.meta)
        out.append(
            Candidate(
                unit_id=uid,
                score=score,
                source="+".join(sources),
                meta=meta,
            )
        )
    out.sort(
        key=lambda x: (
            -x.score,
            0 if "+" in (x.source or "") else 1,
            x.unit_id,
        )
    )
    if top_k is None or top_k <= 0 or len(out) <= top_k:
        return out
    return _balanced_topk(out, top_k)


def merge_candidates_weighted(
    tag_hits: list[Candidate],
    embedding_hits: list[Candidate],
    *,
    w_tag: float = 0.5,
    w_embedding: float = 0.5,
    top_k: int | None = None,
) -> list[Candidate]:
    """
    Tag + Embedding 加权合并：
      final = w_tag * norm(tag) + w_embedding * norm(embedding)
    """
    return merge_candidates_weighted_paths(
        {"tag": tag_hits, "embedding": embedding_hits},
        {"tag": w_tag, "embedding": w_embedding},
        top_k=top_k,
    )


def _balanced_topk(ranked: list[Candidate], k: int) -> list[Candidate]:
    dual = [c for c in ranked if "+" in (c.source or "")]
    tag_only = [c for c in ranked if c.source == "tag"]
    emb_only = [c for c in ranked if c.source == "embedding"]
    other = [
        c
        for c in ranked
        if c.source not in {"tag", "embedding"} and "+" not in (c.source or "")
    ]

    selected: list[Candidate] = []
    seen: set[str] = set()

    def _take(c: Candidate) -> None:
        if c.unit_id in seen or len(selected) >= k:
            return
        seen.add(c.unit_id)
        selected.append(c)

    for c in dual:
        _take(c)
        if len(selected) >= k:
            return selected

    i = j = o = 0
    while len(selected) < k and (
        i < len(tag_only) or j < len(emb_only) or o < len(other)
    ):
        if i < len(tag_only):
            _take(tag_only[i])
            i += 1
        if len(selected) >= k:
            break
        if j < len(emb_only):
            _take(emb_only[j])
            j += 1
        if len(selected) >= k:
            break
        if o < len(other):
            _take(other[o])
            o += 1
    return selected
