"""Tag 召回评分：entity 重合高权重 + keyword 按个数（参数见 config.retrieval.tag_score）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from src.store import get_db, load_config
from src.tokenize import analyze_question
from src.vocabulary import load_vocabulary_terms


@dataclass
class TagScoreConfig:
    """
    tag_score(chunk) =
        entity_weight * |E_q ∩ E_c|
      + entity_hit_bonus * 1[有实体重合]
      + keyword_weight * |K_q ∩ K_c|   （或 Σ weight，见 use_keyword_tfidf）
    """

    entity_weight: float = 10.0
    keyword_weight: float = 1.0
    entity_hit_bonus: float = 5.0
    use_keyword_tfidf: bool = False
    min_score: float = 0.0
    top_k: int = 50


@dataclass
class RetrievalConfig:
    top_k: int = 20
    w_vector: float = 0.5
    w_tag: float = 0.5
    tag_score: TagScoreConfig = field(default_factory=TagScoreConfig)


@dataclass
class QuerySideTags:
    entities: list[str]
    keywords: list[str]
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def resolve_tag_score_config() -> TagScoreConfig:
    cfg = (load_config().get("retrieval") or {}).get("tag_score") or {}
    return TagScoreConfig(
        entity_weight=_env_float(
            "TAG_ENTITY_WEIGHT", float(cfg.get("entity_weight", 10.0))
        ),
        keyword_weight=_env_float(
            "TAG_KEYWORD_WEIGHT", float(cfg.get("keyword_weight", 1.0))
        ),
        entity_hit_bonus=_env_float(
            "TAG_ENTITY_HIT_BONUS", float(cfg.get("entity_hit_bonus", 5.0))
        ),
        use_keyword_tfidf=_env_bool(
            "TAG_USE_KEYWORD_TFIDF",
            bool(cfg.get("use_keyword_tfidf", False)),
        ),
        min_score=_env_float("TAG_MIN_SCORE", float(cfg.get("min_score", 0.0))),
        top_k=_env_int("TAG_TOP_K", int(cfg.get("top_k", 50))),
    )


def resolve_retrieval_config() -> RetrievalConfig:
    cfg = load_config().get("retrieval") or {}
    return RetrievalConfig(
        top_k=_env_int("RETRIEVAL_TOP_K", int(cfg.get("top_k", 20))),
        w_vector=_env_float("RETRIEVAL_W_VECTOR", float(cfg.get("w_vector", 0.5))),
        w_tag=_env_float("RETRIEVAL_W_TAG", float(cfg.get("w_tag", 0.5))),
        tag_score=resolve_tag_score_config(),
    )


def extract_query_tags(question: str) -> QuerySideTags:
    """
    问题侧：实体（出现即用）+ 关键词（与全局 V 求交，排除已是实体的）。
    """
    analysis = analyze_question(question)
    entities = list(analysis.entities)
    entity_set = set(entities)

    try:
        vocab = set(load_vocabulary_terms())
    except Exception:
        vocab = set()

    keywords: list[str] = []
    for t in analysis.tokens:
        if t in entity_set:
            continue
        if vocab and t not in vocab:
            continue
        if t not in keywords:
            keywords.append(t)

    # 无 V 时：用非实体 token 作为 keyword 候选
    if not vocab:
        keywords = [t for t in analysis.tokens if t not in entity_set]

    return QuerySideTags(
        entities=entities,
        keywords=keywords,
        people=list(analysis.people),
        places=list(analysis.places),
        orgs=list(analysis.orgs),
    )


def compute_tag_score(
    *,
    n_entity: int,
    n_keyword: int,
    keyword_weight_sum: float = 0.0,
    cfg: TagScoreConfig | None = None,
) -> float:
    """纯公式，便于单测与调参。"""
    cfg = cfg or resolve_tag_score_config()
    score = 0.0
    if n_entity > 0:
        score += cfg.entity_weight * n_entity
        score += cfg.entity_hit_bonus
    if cfg.use_keyword_tfidf:
        score += cfg.keyword_weight * keyword_weight_sum
    else:
        score += cfg.keyword_weight * n_keyword
    return score


def tag_match(
    question: str,
    *,
    cfg: TagScoreConfig | None = None,
    query_side: QuerySideTags | None = None,
) -> list[dict[str, Any]]:
    """
    倒排匹配 chunk_entity + chunk_term，按公式打分排序。

    返回项：
      id, date, text, score, tag_score, entity_hits, keyword_hits,
      n_entity, n_keyword, score_detail
    """
    cfg = cfg or resolve_tag_score_config()
    qside = query_side or extract_query_tags(question)
    if not qside.entities and not qside.keywords:
        return []

    conn = get_db()
    try:
        # chunk_id → 累计
        entity_hits: dict[str, set[str]] = {}
        keyword_hits: dict[str, set[str]] = {}
        keyword_wsum: dict[str, float] = {}

        if qside.entities:
            placeholders = ",".join("?" * len(qside.entities))
            rows = conn.execute(
                f"""
                SELECT chunk_id, name FROM chunk_entity
                WHERE name IN ({placeholders})
                """,
                qside.entities,
            ).fetchall()
            for r in rows:
                entity_hits.setdefault(r["chunk_id"], set()).add(r["name"])

        if qside.keywords:
            placeholders = ",".join("?" * len(qside.keywords))
            rows = conn.execute(
                f"""
                SELECT chunk_id, term, weight FROM chunk_term
                WHERE term IN ({placeholders})
                """,
                qside.keywords,
            ).fetchall()
            for r in rows:
                cid = r["chunk_id"]
                keyword_hits.setdefault(cid, set()).add(r["term"])
                keyword_wsum[cid] = keyword_wsum.get(cid, 0.0) + float(r["weight"] or 0.0)

        all_ids = set(entity_hits) | set(keyword_hits)
        if not all_ids:
            return []

        scored: list[dict[str, Any]] = []
        for cid in all_ids:
            e_set = entity_hits.get(cid, set())
            k_set = keyword_hits.get(cid, set())
            n_e, n_k = len(e_set), len(k_set)
            wsum = keyword_wsum.get(cid, 0.0)
            score = compute_tag_score(
                n_entity=n_e,
                n_keyword=n_k,
                keyword_weight_sum=wsum,
                cfg=cfg,
            )
            if score < cfg.min_score:
                continue
            scored.append(
                {
                    "id": cid,
                    "tag_score": score,
                    "n_entity": n_e,
                    "n_keyword": n_k,
                    "entity_hits": sorted(e_set),
                    "keyword_hits": sorted(k_set),
                    "score_detail": {
                        "entity_part": (
                            cfg.entity_weight * n_e + (cfg.entity_hit_bonus if n_e else 0.0)
                        ),
                        "keyword_part": (
                            cfg.keyword_weight * wsum
                            if cfg.use_keyword_tfidf
                            else cfg.keyword_weight * n_k
                        ),
                        "entity_weight": cfg.entity_weight,
                        "keyword_weight": cfg.keyword_weight,
                        "entity_hit_bonus": cfg.entity_hit_bonus if n_e else 0.0,
                    },
                }
            )

        scored.sort(key=lambda x: (-x["tag_score"], x["id"]))
        scored = scored[: cfg.top_k]
        if not scored:
            return []

        # 补全 date / text
        ids = [s["id"] for s in scored]
        placeholders = ",".join("?" * len(ids))
        meta = {
            r["id"]: r
            for r in conn.execute(
                f"SELECT id, date, text FROM chunks WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        }
        out: list[dict[str, Any]] = []
        for s in scored:
            row = meta.get(s["id"])
            if not row:
                continue
            item = dict(s)
            item["date"] = row["date"]
            item["text"] = row["text"]
            item["score"] = s["tag_score"]  # 与向量路统一字段名
            item["source"] = "tag"
            out.append(item)
        return out
    finally:
        conn.close()


def merge_vector_and_tag(
    vector_hits: list[dict],
    tag_hits: list[dict],
    *,
    retrieval_cfg: RetrievalConfig | None = None,
) -> list[dict]:
    """
    双路合并：
      final = w_vector * norm(vec) + w_tag * norm(tag)
    缺失一路则该路记 0；按 final 降序取 top_k。
    """
    retrieval_cfg = retrieval_cfg or resolve_retrieval_config()
    w_v = retrieval_cfg.w_vector
    w_t = retrieval_cfg.w_tag

    by_id: dict[str, dict] = {}

    def _norm_map(items: list[dict], key: str) -> dict[str, float]:
        vals = [float(x.get(key) or x.get("score") or 0.0) for x in items]
        lo, hi = (min(vals), max(vals)) if vals else (0.0, 0.0)
        span = (hi - lo) or 1.0
        return {
            x["id"]: (float(x.get(key) or x.get("score") or 0.0) - lo) / span
            for x in items
        }

    vec_n = _norm_map(vector_hits, "score")
    tag_n = _norm_map(tag_hits, "tag_score")

    for r in vector_hits:
        by_id[r["id"]] = {
            **r,
            "vec_score": float(r.get("score") or 0.0),
            "tag_score": 0.0,
            "source": "vector",
        }
    for r in tag_hits:
        if r["id"] in by_id:
            by_id[r["id"]]["tag_score"] = float(r.get("tag_score") or 0.0)
            by_id[r["id"]]["entity_hits"] = r.get("entity_hits", [])
            by_id[r["id"]]["keyword_hits"] = r.get("keyword_hits", [])
            by_id[r["id"]]["n_entity"] = r.get("n_entity", 0)
            by_id[r["id"]]["n_keyword"] = r.get("n_keyword", 0)
            by_id[r["id"]]["score_detail"] = r.get("score_detail")
            by_id[r["id"]]["source"] = "both"
        else:
            by_id[r["id"]] = {
                **r,
                "vec_score": 0.0,
                "source": "tag",
            }

    merged: list[dict] = []
    for cid, item in by_id.items():
        nv = vec_n.get(cid, 0.0)
        nt = tag_n.get(cid, 0.0)
        final = w_v * nv + w_t * nt
        item = dict(item)
        item["score"] = final
        item["score_vector_norm"] = nv
        item["score_tag_norm"] = nt
        merged.append(item)

    merged.sort(key=lambda x: (-x["score"], x.get("date", ""), x["id"]))
    return merged[: retrieval_cfg.top_k]
