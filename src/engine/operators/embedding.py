"""Embedding 召回算子：按主题短语分别 ANN，并集按 max 分合并。"""

from __future__ import annotations

from src.embed import search_similar
from src.engine.candidate import Candidate, merge_candidates
from src.engine.operator import Operator
from src.tag_retrieve import resolve_retrieval_config


class EmbeddingOperator(Operator):
    name = "embedding"

    def __init__(self, top_k: int | None = None):
        self.top_k = top_k

    def _themes(self, query: str, structured) -> list[str]:
        themes: list[str] = []
        if structured is not None:
            raw = getattr(structured, "query_themes", None)
            if callable(raw):
                # property
                try:
                    raw = structured.query_themes
                except Exception:
                    raw = None
            if isinstance(raw, list):
                themes = [str(t).strip() for t in raw if str(t).strip()]
            if not themes:
                sents = getattr(structured, "query_sentences", None) or []
                if isinstance(sents, list):
                    themes = [str(t).strip() for t in sents if str(t).strip()]
        if not themes and query.strip():
            themes = [query.strip()]
        # 至多 3 个主题，避免检索膨胀
        return themes[:3]

    def execute(
        self,
        query: str,
        candidates: list[Candidate],
        *,
        structured=None,
    ) -> list[Candidate]:
        cfg = resolve_retrieval_config()
        k = self.top_k if self.top_k is not None else cfg.top_k
        themes = self._themes(query, structured)
        if not themes:
            return list(candidates)

        # 每个主题多取一些 sentence，留给后续按 chunk 聚合截断
        per_theme_k = max(int(k), 8)

        merged = list(candidates)
        for theme in themes:
            try:
                hits = search_similar(theme, top_k=per_theme_k)
            except Exception as exc:
                print(f"  [warn] EmbeddingOperator theme={theme!r} 失败: {exc}")
                continue
            new = [
                Candidate(
                    unit_id=h["id"],
                    score=float(h.get("score") or 0.0),
                    source="embedding",
                    meta={
                        "parent_chunk_id": h.get("chunk_id") or "",
                        "sentence_text": h.get("text") or "",
                        "theme": theme,
                    },
                )
                for h in hits
                if h.get("id")
            ]
            # 同 sentence：取更高分（多主题命中自然抬升）
            merged = merge_candidates(merged, new)

        return merged
