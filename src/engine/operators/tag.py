"""Tag 召回算子：独立 tag/entity 检索，与入参 Candidate 并集合并。"""

from __future__ import annotations

from src.engine.candidate import Candidate, merge_candidates
from src.engine.operator import Operator
from src.tag_retrieve import resolve_tag_score_config, tag_match


class TagOperator(Operator):
    name = "tag"

    def __init__(self, top_k: int | None = None):
        self.top_k = top_k

    def execute(
        self,
        query: str,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        cfg = resolve_tag_score_config()
        if self.top_k is not None:
            cfg.top_k = self.top_k
        try:
            hits = tag_match(query, cfg=cfg)
        except Exception as exc:
            print(f"  [warn] TagOperator 失败: {exc}")
            return list(candidates)

        new = [
            Candidate(
                chunk_id=h["id"],
                score=float(h.get("tag_score") or h.get("score") or 0.0),
                source="tag",
            )
            for h in hits
            if h.get("id")
        ]
        return merge_candidates(candidates, new)
