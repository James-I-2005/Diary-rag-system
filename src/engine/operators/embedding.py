"""Embedding 召回算子：独立向量检索，与入参 Candidate 并集合并。"""

from __future__ import annotations

from src.embed import search_similar
from src.engine.candidate import Candidate, merge_candidates
from src.engine.operator import Operator
from src.tag_retrieve import resolve_retrieval_config


class EmbeddingOperator(Operator):
    name = "embedding"

    def __init__(self, top_k: int | None = None):
        self.top_k = top_k

    def execute(
        self,
        query: str,
        candidates: list[Candidate],
        *,
        structured=None,
    ) -> list[Candidate]:
        cfg = resolve_retrieval_config()
        k = self.top_k if self.top_k is not None else cfg.top_k
        try:
            hits = search_similar(query, top_k=k)
        except Exception as exc:
            print(f"  [warn] EmbeddingOperator 失败: {exc}")
            return list(candidates)

        new = [
            Candidate(
                chunk_id=h["id"],
                score=float(h.get("score") or 0.0),
                source="embedding",
            )
            for h in hits
            if h.get("id")
        ]
        return merge_candidates(candidates, new)
