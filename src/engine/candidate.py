"""Pipeline 中流动的轻量 Candidate。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    chunk_id: str
    score: float = 0.0
    source: str = ""  # 调试用：tag / embedding / view / tag+view
    meta: dict[str, Any] = field(default_factory=dict)


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
    return sorted(by_id.values(), key=lambda x: (-x.score, x.chunk_id))


def _minmax_norm(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi <= lo:
        # 全部相同：视为满分，避免加权一路被清零
        return [1.0] * len(scores)
    span = hi - lo
    return [(s - lo) / span for s in scores]


def merge_candidates_weighted_paths(
    paths: dict[str, list[Candidate]],
    weights: dict[str, float],
    *,
    top_k: int | None = None,
) -> list[Candidate]:
    """多路归一化加权合并；paths/weights 的 key 应对齐（如 tag、embedding、view）。"""
    norm_maps: dict[str, dict[str, float]] = {}
    for name, hits in paths.items():
        norm_maps[name] = {
            c.chunk_id: s
            for c, s in zip(hits, _minmax_norm([float(c.score) for c in hits]))
        }

    ids: set[str] = set()
    for m in norm_maps.values():
        ids |= set(m.keys())

    out: list[Candidate] = []
    for cid in ids:
        score = 0.0
        sources: list[str] = []
        meta: dict = {}
        for name, w in weights.items():
            if cid in norm_maps.get(name, {}):
                score += w * norm_maps[name][cid]
                sources.append(name)
                for c in paths.get(name, []):
                    if c.chunk_id == cid and c.meta:
                        meta.update(c.meta)
        out.append(
            Candidate(
                chunk_id=cid,
                score=score,
                source="+".join(sources),
                meta=meta,
            )
        )
    out.sort(
        key=lambda x: (
            -x.score,
            0 if "+" in (x.source or "") else 1,
            x.chunk_id,
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
    缺失一路记 0；各自 min-max 归一化到 [0,1]。
    同分时优先双路命中，并在截断时均衡两路，避免一路占满 top_k。
    """
    tag_n = _minmax_norm([float(c.score) for c in tag_hits])
    emb_n = _minmax_norm([float(c.score) for c in embedding_hits])
    tag_map = {c.chunk_id: tag_n[i] for i, c in enumerate(tag_hits)}
    emb_map = {c.chunk_id: emb_n[i] for i, c in enumerate(embedding_hits)}

    ids = set(tag_map) | set(emb_map)
    out: list[Candidate] = []
    for cid in ids:
        nt = tag_map.get(cid, 0.0)
        ne = emb_map.get(cid, 0.0)
        sources: list[str] = []
        if cid in tag_map:
            sources.append("tag")
        if cid in emb_map:
            sources.append("embedding")
        out.append(
            Candidate(
                chunk_id=cid,
                score=w_tag * nt + w_embedding * ne,
                source="+".join(sources),
            )
        )
    out.sort(
        key=lambda x: (
            -x.score,
            0 if "+" in (x.source or "") else 1,
            x.chunk_id,
        )
    )
    if top_k is None or top_k <= 0 or len(out) <= top_k:
        return out
    return _balanced_topk(out, top_k)


def _balanced_topk(ranked: list[Candidate], k: int) -> list[Candidate]:
    """截断时：双路优先，其余 tag/embedding 轮询，避免单路占满。"""
    dual = [c for c in ranked if "+" in (c.source or "")]
    tag_only = [c for c in ranked if c.source == "tag"]
    emb_only = [c for c in ranked if c.source == "embedding"]
    view_only = [c for c in ranked if c.source == "view"]
    other = [
        c
        for c in ranked
        if c.source not in {"tag", "embedding", "view"}
        and "+" not in (c.source or "")
    ]

    selected: list[Candidate] = []
    seen: set[str] = set()

    def _take(c: Candidate) -> None:
        if c.chunk_id in seen or len(selected) >= k:
            return
        seen.add(c.chunk_id)
        selected.append(c)

    for c in dual:
        _take(c)
        if len(selected) >= k:
            return selected

    i = j = v = o = 0
    while len(selected) < k and (
        i < len(tag_only) or j < len(emb_only) or v < len(view_only) or o < len(other)
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
        if v < len(view_only):
            _take(view_only[v])
            v += 1
        if len(selected) >= k:
            break
        if o < len(other):
            _take(other[o])
            o += 1
    return selected
