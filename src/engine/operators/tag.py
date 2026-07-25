"""Tag 召回算子：chunk 打分后展开为 rag-sentences。"""

from __future__ import annotations

from src.engine.candidate import Candidate, merge_candidates
from src.engine.operator import Operator
from src.rag_sentences import sentences_for_chunks
from src.tag_retrieve import resolve_tag_score_config, tag_match


class TagOperator(Operator):
    name = "tag"

    def __init__(self, top_k: int | None = None):
        self.top_k = top_k

    def execute(
        self,
        query: str,
        candidates: list[Candidate],
        *,
        structured=None,
    ) -> list[Candidate]:
        cfg = resolve_tag_score_config()
        if self.top_k is not None:
            cfg.top_k = self.top_k
        try:
            hits = tag_match(query, cfg=cfg)
        except Exception as exc:
            print(f"  [warn] TagOperator 失败: {exc}")
            return list(candidates)

        chunk_scores = {
            h["id"]: float(h.get("tag_score") or h.get("score") or 0.0)
            for h in hits
            if h.get("id")
        }
        if not chunk_scores:
            return list(candidates)

        by_chunk = sentences_for_chunks(list(chunk_scores.keys()))
        new: list[Candidate] = []
        for cid, score in chunk_scores.items():
            sents = by_chunk.get(cid) or []
            if not sents:
                # 尚无 paraphrase：退化为 chunk 级候选（unit_id=chunk_id）
                new.append(
                    Candidate(
                        unit_id=cid,
                        score=score,
                        source="tag",
                        meta={"parent_chunk_id": cid, "fallback_chunk": True},
                    )
                )
                continue
            for s in sents:
                new.append(
                    Candidate(
                        unit_id=s.id,
                        score=score,
                        source="tag",
                        meta={
                            "parent_chunk_id": cid,
                            "sentence_text": s.text,
                        },
                    )
                )

        merged = merge_candidates(candidates, new)
        k = self.top_k if self.top_k is not None else cfg.top_k
        return merged[:k]
