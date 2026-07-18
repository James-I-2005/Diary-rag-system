"""候选词表 V：全库分词统计、DF/TF 过滤与排序（参数见 config.yaml / .env）。"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.store import get_db, load_config, resolve_path
from src.tokenize import analyze_text, token_counts

SortBy = Literal["total_tf", "df", "avg_tf", "tf_idf"]


@dataclass
class VocabularyConfig:
    """词表构建策略；环境变量可覆盖同名 config（见 resolve_vocabulary_config）。"""

    vocab_size: int = 5000
    min_df_ratio: float = 0.02
    max_df_ratio: float = 0.80
    min_df_abs: int = 2
    min_total_tf: int = 2
    min_token_len: int = 2
    max_token_len: int = 12
    exclude_single_char: bool = True
    sort_by: SortBy = "tf_idf"
    output_path: str = "data/vocabulary.json"
    exclude_entity_terms: bool = True

    def min_df(self, n_docs: int) -> int:
        by_ratio = math.ceil(self.min_df_ratio * n_docs) if n_docs else 1
        return max(self.min_df_abs, by_ratio, 1)

    def max_df(self, n_docs: int) -> int:
        if n_docs <= 0:
            return 0
        return max(int(self.max_df_ratio * n_docs), 1)


@dataclass
class TermRecord:
    term: str
    df: int
    total_tf: int

    @property
    def avg_tf(self) -> float:
        return self.total_tf / self.df if self.df else 0.0

    def score(self, n_docs: int, sort_by: SortBy) -> float:
        if sort_by == "total_tf":
            return float(self.total_tf)
        if sort_by == "df":
            return float(self.df)
        if sort_by == "avg_tf":
            return self.avg_tf
        # tf_idf：语料级权重，用于排序进 V（非 chunk 级 TF-IDF）
        idf = math.log((n_docs + 1) / (self.df + 1)) + 1.0
        return float(self.total_tf) * idf


@dataclass
class VocabularyBuildResult:
    terms: list[str]
    records: list[TermRecord] = field(default_factory=list)
    n_docs: int = 0
    n_candidates: int = 0
    config: VocabularyConfig | None = None


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name, "").strip()
    return raw if raw else default


def resolve_vocabulary_config() -> VocabularyConfig:
    """读取 config.yaml 的 vocabulary 段，并用 VOCAB_* 环境变量覆盖。"""
    cfg = load_config().get("vocabulary") or {}
    sort_by = _env_str("VOCAB_SORT_BY", cfg.get("sort_by", "tf_idf"))
    if sort_by not in ("total_tf", "df", "avg_tf", "tf_idf"):
        sort_by = "tf_idf"

    return VocabularyConfig(
        vocab_size=_env_int("VOCAB_SIZE", int(cfg.get("vocab_size", 5000))),
        min_df_ratio=_env_float("VOCAB_MIN_DF_RATIO", float(cfg.get("min_df_ratio", 0.02))),
        max_df_ratio=_env_float("VOCAB_MAX_DF_RATIO", float(cfg.get("max_df_ratio", 0.80))),
        min_df_abs=_env_int("VOCAB_MIN_DF_ABS", int(cfg.get("min_df_abs", 2))),
        min_total_tf=_env_int("VOCAB_MIN_TOTAL_TF", int(cfg.get("min_total_tf", 2))),
        min_token_len=_env_int("VOCAB_MIN_TOKEN_LEN", int(cfg.get("min_token_len", 2))),
        max_token_len=_env_int("VOCAB_MAX_TOKEN_LEN", int(cfg.get("max_token_len", 12))),
        exclude_single_char=_env_bool(
            "VOCAB_EXCLUDE_SINGLE_CHAR",
            bool(cfg.get("exclude_single_char", True)),
        ),
        sort_by=sort_by,  # type: ignore[arg-type]
        output_path=_env_str(
            "VOCAB_OUTPUT_PATH",
            cfg.get("output_path", "data/vocabulary.json"),
        ),
        exclude_entity_terms=_env_bool(
            "VOCAB_EXCLUDE_ENTITY_TERMS",
            bool(cfg.get("exclude_entity_terms", True)),
        ),
    )


def _token_passes_filters(term: str, cfg: VocabularyConfig) -> bool:
    if not term:
        return False
    if len(term) < cfg.min_token_len or len(term) > cfg.max_token_len:
        return False
    if cfg.exclude_single_char and len(term) == 1:
        return False
    return True


def collect_term_stats(
    texts: list[str],
    cfg: VocabularyConfig | None = None,
) -> tuple[dict[str, TermRecord], int]:
    """对多段文本统计 df / total_tf。"""
    cfg = cfg or resolve_vocabulary_config()
    n_docs = len(texts)
    df_counter: Counter[str] = Counter()
    tf_counter: Counter[str] = Counter()
    entity_terms: set[str] = set()

    for text in texts:
        if not text or not text.strip():
            continue
        analysis = analyze_text(text, remove_stopwords=True)
        if cfg.exclude_entity_terms:
            entity_terms.update(analysis.entities)

        doc_tf = token_counts(text, remove_stopwords=True)
        for term, cnt in doc_tf.items():
            if not _token_passes_filters(term, cfg):
                continue
            if cfg.exclude_entity_terms and term in entity_terms:
                continue
            tf_counter[term] += cnt

        for term in doc_tf.keys():
            if not _token_passes_filters(term, cfg):
                continue
            if cfg.exclude_entity_terms and term in entity_terms:
                continue
            df_counter[term] += 1

    records: dict[str, TermRecord] = {}
    for term, df in df_counter.items():
        records[term] = TermRecord(term=term, df=df, total_tf=tf_counter[term])

    return records, n_docs


def filter_and_rank_terms(
    records: dict[str, TermRecord],
    n_docs: int,
    cfg: VocabularyConfig | None = None,
) -> list[TermRecord]:
    """按 DF/TF 阈值过滤，再按 sort_by 排序，取 vocab_size。"""
    cfg = cfg or resolve_vocabulary_config()
    min_df = cfg.min_df(n_docs)
    max_df = cfg.max_df(n_docs)

    candidates: list[TermRecord] = []
    for rec in records.values():
        if rec.df < min_df or rec.df > max_df:
            continue
        if rec.total_tf < cfg.min_total_tf:
            continue
        candidates.append(rec)

    candidates.sort(
        key=lambda r: r.score(n_docs, cfg.sort_by),
        reverse=True,
    )
    return candidates[: cfg.vocab_size]


def build_vocabulary_from_texts(
    texts: list[str],
    cfg: VocabularyConfig | None = None,
) -> VocabularyBuildResult:
    cfg = cfg or resolve_vocabulary_config()
    records_map, n_docs = collect_term_stats(texts, cfg)
    ranked = filter_and_rank_terms(records_map, n_docs, cfg)
    return VocabularyBuildResult(
        terms=[r.term for r in ranked],
        records=ranked,
        n_docs=n_docs,
        n_candidates=len(records_map),
        config=cfg,
    )


def build_vocabulary_from_db(cfg: VocabularyConfig | None = None) -> VocabularyBuildResult:
    cfg = cfg or resolve_vocabulary_config()
    conn = get_db()
    rows = conn.execute("SELECT text FROM chunks ORDER BY date, id").fetchall()
    conn.close()
    texts = [r["text"] for r in rows if r["text"]]
    if not texts:
        raise ValueError("chunks 表为空，请先运行 ingest")
    return build_vocabulary_from_texts(texts, cfg)


def save_vocabulary(
    result: VocabularyBuildResult,
    path: Path | None = None,
    *,
    review_meta: dict[str, Any] | None = None,
    persist_db: bool = True,
) -> Path:
    cfg = result.config or resolve_vocabulary_config()
    out = path or resolve_path(cfg.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    built_at = datetime.now(timezone.utc).isoformat()
    stats = [
        {
            "term": r.term,
            "df": r.df,
            "total_tf": r.total_tf,
            "score": r.score(result.n_docs, cfg.sort_by),
        }
        for r in result.records
    ]
    payload: dict[str, Any] = {
        "built_at": built_at,
        "n_docs": result.n_docs,
        "n_candidates": result.n_candidates,
        "n_terms": len(result.terms),
        "config": asdict(cfg),
        "terms": result.terms,
        "stats": stats,
    }
    if review_meta:
        payload["review"] = review_meta
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if persist_db:
        from src.lexicon_db import save_vocab_build

        build_id = save_vocab_build(
            terms=result.terms,
            stats=stats,
            n_docs=result.n_docs,
            n_candidates=result.n_candidates,
            config=asdict(cfg),
            review=review_meta,
            built_at=built_at,
            source="build_vocabulary",
            mark_active=True,
        )
        payload["build_id"] = build_id
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return out


def load_vocabulary(path: Path | None = None, *, prefer_db: bool = True) -> dict[str, Any]:
    """优先读 active 词表 DB；无则回退 JSON。"""
    if prefer_db:
        try:
            from src.lexicon_db import connect_lexicon_db, load_active_vocab_terms

            conn = connect_lexicon_db()
            try:
                active = True if conn.backend == "postgres" else 1
                build = conn.fetchone_dict(
                    """
                    SELECT id, built_at, n_docs, n_candidates, n_terms, config_json, review_json
                    FROM vocab_builds WHERE is_active = ?
                    ORDER BY built_at DESC LIMIT 1
                    """,
                    (active,),
                )
                terms_rows = load_active_vocab_terms(conn)
                if build and terms_rows:
                    return {
                        "built_at": build["built_at"],
                        "n_docs": build["n_docs"],
                        "n_candidates": build["n_candidates"],
                        "n_terms": build["n_terms"],
                        "build_id": build["id"],
                        "config": json.loads(build["config_json"] or "{}"),
                        "review": json.loads(build["review_json"] or "null"),
                        "terms": [r["term"] for r in terms_rows],
                        "stats": terms_rows,
                        "source": "lexicon_db",
                    }
            finally:
                conn.close()
        except Exception:
            pass

    cfg = resolve_vocabulary_config()
    p = path or resolve_path(cfg.output_path)
    if not p.is_file():
        raise FileNotFoundError(f"词表不存在: {p}，请先运行 scripts/build_vocabulary.py")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["source"] = "json"
    return data


def load_vocabulary_terms(path: Path | None = None) -> list[str]:
    data = load_vocabulary(path)
    return list(data.get("terms") or [])
