"""Memory View 召回算子：ANN on diary_views → 按 chunk_id 聚合。"""

from __future__ import annotations

import os
from typing import Any

from src.embed import search_views
from src.engine.candidate import Candidate, merge_candidates
from src.engine.operator import Operator
from src.memory_views import VALID_VIEW_TYPES
from src.tag_retrieve import resolve_retrieval_config


def _views_enabled() -> bool:
    env = os.getenv("MEMORY_VIEWS_ENABLED", "").strip().lower()
    if env:
        return env in {"1", "true", "yes", "on"}
    return True


def _resolve_view_query(query: str, structured: Any) -> str:
    if structured is not None:
        eq = getattr(structured, "embedding_query", "") or ""
        if eq.strip():
            return eq.strip()
        rq = getattr(structured, "view_retrieval_query", None)
        if callable(rq):
            text = rq()
            if text.strip():
                return text.strip()
    return query.strip()


def _resolve_filters(structured: Any) -> tuple[list[str] | None, str | None, str | None]:
    if structured is None:
        return None, None, None
    rep = getattr(structured, "query_representation", None)
    if rep is None:
        intent = getattr(structured, "intent", "unknown")
        return _default_hints_for_intent(intent), None, None

    hints = list(getattr(rep, "view_type_hints", None) or [])
    valid = [h for h in hints if h in VALID_VIEW_TYPES]
    if not valid:
        intent = getattr(structured, "intent", "unknown")
        valid = _default_hints_for_intent(intent) or []

    tr = getattr(rep, "time_range", None) or {}
    start = tr.get("start") if isinstance(tr, dict) else None
    end = tr.get("end") if isinstance(tr, dict) else None
    return (valid or None), start, end


def _default_hints_for_intent(intent: str) -> list[str] | None:
    mapping = {
        "memory_recall": ["event", "narrative"],
        "memory_search": ["event", "future_query"],
        "summary": ["growth", "identity", "event"],
    }
    return mapping.get(intent)


class ViewOperator(Operator):
    name = "view"

    def __init__(self, top_k: int | None = None):
        self.top_k = top_k

    def execute(
        self,
        query: str,
        candidates: list[Candidate],
        *,
        structured=None,
    ) -> list[Candidate]:
        if not _views_enabled():
            return list(candidates)

        cfg = resolve_retrieval_config()
        k = self.top_k if self.top_k is not None else cfg.top_k
        view_query = _resolve_view_query(query, structured)
        view_types, date_start, date_end = _resolve_filters(structured)

        try:
            hits = search_views(
                view_query,
                top_k=k,
                view_types=view_types,
                date_start=date_start,
                date_end=date_end,
            )
        except Exception as exc:
            print(f"  [warn] ViewOperator 失败: {exc}")
            return list(candidates)

        if not hits:
            return list(candidates)

        by_chunk: dict[str, dict] = {}
        for h in hits:
            cid = h.get("chunk_id") or ""
            if not cid:
                continue
            score = float(h.get("score") or 0.0)
            entry = by_chunk.get(cid)
            view_item = {
                "view_id": h.get("view_id"),
                "view_type": h.get("view_type"),
                "content": h.get("text"),
                "score": score,
            }
            if entry is None:
                by_chunk[cid] = {
                    "score": score,
                    "matched_views": [view_item],
                }
            else:
                if score > entry["score"]:
                    entry["score"] = score
                entry["matched_views"].append(view_item)

        new: list[Candidate] = []
        for cid, data in by_chunk.items():
            views = sorted(
                data["matched_views"],
                key=lambda x: -float(x.get("score") or 0),
            )[:3]
            new.append(
                Candidate(
                    chunk_id=cid,
                    score=float(data["score"]),
                    source="view",
                    meta={"matched_views": views},
                )
            )

        merged = merge_candidates(candidates, new)
        return sorted(merged, key=lambda x: (-x.score, x.chunk_id))[:k]
