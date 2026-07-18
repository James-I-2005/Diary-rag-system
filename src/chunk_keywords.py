"""按全局词表 V 为每个 chunk 提取 TF-IDF keywords（仅 V 内词）。"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.store import get_db, load_config, resolve_path
from src.tokenize import token_counts
from src.vocabulary import load_vocabulary


@dataclass
class ChunkKeywordConfig:
    keywords_per_chunk: int = 20
    output_path: str = "data/chunk_keywords.json"
    min_weight: float = 0.0


@dataclass
class WeightedTerm:
    term: str
    weight: float
    tf: int = 0


@dataclass
class ChunkKeywordRow:
    chunk_id: str
    date: str
    keywords: list[WeightedTerm] = field(default_factory=list)
    preview: str = ""


@dataclass
class ChunkKeywordBuildResult:
    rows: list[ChunkKeywordRow]
    n_chunks: int = 0
    n_tagged: int = 0
    vocab_size: int = 0
    n_docs: int = 0
    config: ChunkKeywordConfig | None = None


def resolve_chunk_keyword_config() -> ChunkKeywordConfig:
    cfg = load_config().get("vocabulary") or {}
    tags_cfg = load_config().get("tags") or {}

    def _int(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        return int(raw) if raw else default

    def _float(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        return float(raw) if raw else default

    def _str(name: str, default: str) -> str:
        raw = os.getenv(name, "").strip()
        return raw if raw else default

    return ChunkKeywordConfig(
        keywords_per_chunk=_int(
            "KEYWORDS_PER_CHUNK",
            int(cfg.get("keywords_per_chunk") or tags_cfg.get("keywords_per_chunk") or 20),
        ),
        output_path=_str(
            "CHUNK_KEYWORDS_OUTPUT",
            cfg.get("chunk_keywords_output", "data/chunk_keywords.json"),
        ),
        min_weight=_float(
            "KEYWORDS_MIN_WEIGHT",
            float(cfg.get("keywords_min_weight") or 0.0),
        ),
    )


def _load_vocab_idf() -> tuple[set[str], dict[str, float], int]:
    """返回 (V集合, term→idf, n_docs)。"""
    data = load_vocabulary(prefer_db=True)
    terms = [t for t in (data.get("terms") or []) if t]
    if not terms:
        raise ValueError("全局词表 V 为空，请先运行 scripts/build_vocabulary.py")

    n_docs = int(data.get("n_docs") or 0)
    if n_docs <= 0:
        conn = get_db()
        n_docs = int(conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"])
        conn.close()

    df_map: dict[str, int] = {}
    for s in data.get("stats") or []:
        term = s.get("term")
        if term:
            df_map[term] = int(s.get("df") or 0)

    idf: dict[str, float] = {}
    vocab = set(terms)
    for term in vocab:
        df = df_map.get(term, 1)
        idf[term] = math.log((n_docs + 1) / (df + 1)) + 1.0

    return vocab, idf, n_docs


def extract_keywords_for_text(
    text: str,
    vocab: set[str],
    idf: dict[str, float],
    *,
    top_k: int = 20,
    min_weight: float = 0.0,
) -> list[WeightedTerm]:
    """分词后与 V 求交，按 TF-IDF 取 Top-K。"""
    counts = token_counts(text, remove_stopwords=True)
    scored: list[WeightedTerm] = []
    for term, tf in counts.items():
        if term not in vocab:
            continue
        weight = float(tf) * idf.get(term, 1.0)
        if weight < min_weight:
            continue
        scored.append(WeightedTerm(term=term, weight=weight, tf=int(tf)))

    scored.sort(key=lambda w: (-w.weight, w.term))
    return scored[:top_k]


def build_chunk_keywords(
    cfg: ChunkKeywordConfig | None = None,
) -> ChunkKeywordBuildResult:
    cfg = cfg or resolve_chunk_keyword_config()
    vocab, idf, n_docs = _load_vocab_idf()

    conn = get_db()
    rows_db = conn.execute(
        "SELECT id, date, text FROM chunks ORDER BY date, id"
    ).fetchall()
    conn.close()
    if not rows_db:
        raise ValueError("chunks 表为空，请先运行 ingest")

    rows: list[ChunkKeywordRow] = []
    n_tagged = 0
    for r in rows_db:
        text = r["text"] or ""
        kws = extract_keywords_for_text(
            text,
            vocab,
            idf,
            top_k=cfg.keywords_per_chunk,
            min_weight=cfg.min_weight,
        )
        if kws:
            n_tagged += 1
        preview = text.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:80] + "…"
        rows.append(
            ChunkKeywordRow(
                chunk_id=r["id"],
                date=r["date"],
                keywords=kws,
                preview=preview,
            )
        )

    return ChunkKeywordBuildResult(
        rows=rows,
        n_chunks=len(rows),
        n_tagged=n_tagged,
        vocab_size=len(vocab),
        n_docs=n_docs,
        config=cfg,
    )


def save_chunk_keywords_db(result: ChunkKeywordBuildResult) -> None:
    """写入 diary.db：chunk_tags.keywords + chunk_term 倒排。"""
    conn = get_db()
    try:
        # 全量重建倒排
        conn.execute("DELETE FROM chunk_term")

        for row in result.rows:
            kw_json = json.dumps(
                [{"term": w.term, "weight": round(w.weight, 4), "tf": w.tf} for w in row.keywords],
                ensure_ascii=False,
            )

            # 保留旧 LLM 字段：若已有行则 UPDATE keywords；否则插入空壳
            existing = conn.execute(
                "SELECT chunk_id FROM chunk_tags WHERE chunk_id = ?",
                (row.chunk_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE chunk_tags
                    SET keywords = ?, tag_method = 'tfidf', extracted_at = datetime('now')
                    WHERE chunk_id = ?
                    """,
                    (kw_json, row.chunk_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO chunk_tags
                    (chunk_id, topics, activities, emotions, food_mentions, people,
                     is_touching_moment, touching_summary, keywords, tag_method)
                    VALUES (?, '[]', '[]', '[]', '[]', '[]', 0, '', ?, 'tfidf')
                    """,
                    (row.chunk_id, kw_json),
                )

            for w in row.keywords:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO chunk_term (term, chunk_id, weight)
                    VALUES (?, ?, ?)
                    """,
                    (w.term, row.chunk_id, float(w.weight)),
                )

        conn.commit()
    finally:
        conn.close()


def save_chunk_keywords_json(
    result: ChunkKeywordBuildResult,
    path: Path | None = None,
) -> Path:
    cfg = result.config or resolve_chunk_keyword_config()
    out = path or resolve_path(cfg.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "n_chunks": result.n_chunks,
        "n_tagged": result.n_tagged,
        "vocab_size": result.vocab_size,
        "n_docs": result.n_docs,
        "keywords_per_chunk": cfg.keywords_per_chunk,
        "chunks": [
            {
                "chunk_id": r.chunk_id,
                "date": r.date,
                "preview": r.preview,
                "keywords": [
                    {"term": w.term, "weight": round(w.weight, 4), "tf": w.tf}
                    for w in r.keywords
                ],
            }
            for r in result.rows
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def build_and_save_chunk_keywords(
    cfg: ChunkKeywordConfig | None = None,
) -> tuple[ChunkKeywordBuildResult, Path]:
    result = build_chunk_keywords(cfg)
    save_chunk_keywords_db(result)
    path = save_chunk_keywords_json(result)
    return result, path
