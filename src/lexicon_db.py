"""词表 / 停用词持久化：JSON/txt 供人审，数据库供查询与增补（SQLite 或 PostgreSQL）。"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from src.store import ROOT, load_config, resolve_path

Backend = Literal["sqlite", "postgres"]


@dataclass
class LexiconDbConfig:
    backend: Backend = "sqlite"
    sqlite_path: str = "data/lexicon.db"
    database_url: str = ""  # postgres://... 或 postgresql://...


def resolve_lexicon_db_config() -> LexiconDbConfig:
    """读取 config.yaml lexicon.db + 环境变量。"""
    cfg = (load_config().get("lexicon") or {}).get("db") or {}
    url = (
        os.getenv("LEXICON_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or (cfg.get("database_url") or "").strip()
    )
    backend_raw = (
        os.getenv("LEXICON_DB_BACKEND", "").strip()
        or (cfg.get("backend") or "").strip()
        or ("postgres" if url.startswith(("postgres://", "postgresql://")) else "sqlite")
    ).lower()
    backend: Backend = "postgres" if backend_raw in {"postgres", "postgresql", "pg"} else "sqlite"
    return LexiconDbConfig(
        backend=backend,
        sqlite_path=os.getenv("LEXICON_SQLITE_PATH", "").strip()
        or cfg.get("sqlite_path", "data/lexicon.db"),
        database_url=url,
    )


class LexiconConnection:
    """统一 SQLite / PostgreSQL 的最小执行层（? → 后端占位符）。"""

    def __init__(self, backend: Backend, raw: Any):
        self.backend = backend
        self.raw = raw

    def _sql(self, sql: str) -> str:
        if self.backend == "postgres":
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql: str, params: tuple | list = ()) -> Any:
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), params)
        return cur

    def executemany(self, sql: str, seq: list[tuple]) -> Any:
        cur = self.raw.cursor()
        cur.executemany(self._sql(sql), seq)
        return cur

    def executescript(self, script: str) -> None:
        if self.backend == "sqlite":
            self.raw.executescript(script)
            return
        # PG：按分号拆分执行（脚本不含函数体）
        cur = self.raw.cursor()
        for stmt in script.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)

    def commit(self) -> None:
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()

    def fetchall_dicts(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        if self.backend == "sqlite":
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in rows]

    def fetchone_dict(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.fetchall_dicts(sql, params)
        return rows[0] if rows else None


def _sqlite_schema() -> str:
    return """
        CREATE TABLE IF NOT EXISTS vocab_builds (
            id TEXT PRIMARY KEY,
            built_at TEXT NOT NULL,
            n_docs INTEGER NOT NULL DEFAULT 0,
            n_candidates INTEGER NOT NULL DEFAULT 0,
            n_terms INTEGER NOT NULL DEFAULT 0,
            config_json TEXT,
            review_json TEXT,
            is_active INTEGER NOT NULL DEFAULT 0,
            source TEXT DEFAULT 'build_vocabulary',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS vocab_terms (
            build_id TEXT NOT NULL,
            term TEXT NOT NULL,
            df INTEGER NOT NULL DEFAULT 0,
            total_tf INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            rank INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (build_id, term),
            FOREIGN KEY (build_id) REFERENCES vocab_builds(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_vocab_terms_term ON vocab_terms(term);
        CREATE INDEX IF NOT EXISTS idx_vocab_builds_active ON vocab_builds(is_active);

        CREATE TABLE IF NOT EXISTS stopwords (
            term TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'manual',
            comment TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_stopwords_source ON stopwords(source);

        CREATE TABLE IF NOT EXISTS entities (
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            df INTEGER NOT NULL DEFAULT 0,
            total_tf INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'extract',
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (name, entity_type)
        );
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
        CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
        """


def _postgres_schema() -> str:
    return """
        CREATE TABLE IF NOT EXISTS vocab_builds (
            id TEXT PRIMARY KEY,
            built_at TIMESTAMPTZ NOT NULL,
            n_docs INTEGER NOT NULL DEFAULT 0,
            n_candidates INTEGER NOT NULL DEFAULT 0,
            n_terms INTEGER NOT NULL DEFAULT 0,
            config_json TEXT,
            review_json TEXT,
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            source TEXT DEFAULT 'build_vocabulary',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS vocab_terms (
            build_id TEXT NOT NULL REFERENCES vocab_builds(id) ON DELETE CASCADE,
            term TEXT NOT NULL,
            df INTEGER NOT NULL DEFAULT 0,
            total_tf INTEGER NOT NULL DEFAULT 0,
            score DOUBLE PRECISION NOT NULL DEFAULT 0,
            rank INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (build_id, term)
        );
        CREATE INDEX IF NOT EXISTS idx_vocab_terms_term ON vocab_terms(term);
        CREATE INDEX IF NOT EXISTS idx_vocab_builds_active ON vocab_builds(is_active);

        CREATE TABLE IF NOT EXISTS stopwords (
            term TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'manual',
            comment TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_stopwords_source ON stopwords(source);

        CREATE TABLE IF NOT EXISTS entities (
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            df INTEGER NOT NULL DEFAULT 0,
            total_tf INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'extract',
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (name, entity_type)
        );
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
        CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
        """


def connect_lexicon_db(cfg: LexiconDbConfig | None = None) -> LexiconConnection:
    cfg = cfg or resolve_lexicon_db_config()
    if cfg.backend == "postgres":
        if not cfg.database_url:
            raise ValueError(
                "lexicon 后端为 postgres，但未设置 LEXICON_DATABASE_URL / DATABASE_URL"
            )
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError(
                "使用 PostgreSQL 需安装: pip install 'psycopg[binary]>=3.1'"
            ) from exc
        raw = psycopg.connect(cfg.database_url)
        conn = LexiconConnection("postgres", raw)
        conn.executescript(_postgres_schema())
        conn.commit()
        return conn

    path = resolve_path(cfg.sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    conn = LexiconConnection("sqlite", raw)
    conn.executescript(_sqlite_schema())
    conn.commit()
    return conn


@contextmanager
def lexicon_db(cfg: LexiconDbConfig | None = None) -> Iterator[LexiconConnection]:
    conn = connect_lexicon_db(cfg)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.raw.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_vocab_build(
    *,
    terms: list[str],
    stats: list[dict[str, Any]],
    n_docs: int,
    n_candidates: int,
    config: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
    built_at: str | None = None,
    source: str = "build_vocabulary",
    mark_active: bool = True,
    conn: LexiconConnection | None = None,
) -> str:
    """写入一次词表构建；可选设为当前 active。返回 build_id。"""
    build_id = str(uuid.uuid4())
    built_at = built_at or _now_iso()
    own = conn is None
    if own:
        conn = connect_lexicon_db()
    assert conn is not None

    try:
        if mark_active:
            if conn.backend == "postgres":
                conn.execute("UPDATE vocab_builds SET is_active = FALSE WHERE is_active = TRUE")
            else:
                conn.execute("UPDATE vocab_builds SET is_active = 0 WHERE is_active = 1")

        active_val: Any = True if conn.backend == "postgres" else 1
        inactive_val: Any = False if conn.backend == "postgres" else 0
        conn.execute(
            """
            INSERT INTO vocab_builds
            (id, built_at, n_docs, n_candidates, n_terms, config_json, review_json, is_active, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_id,
                built_at,
                n_docs,
                n_candidates,
                len(terms),
                json.dumps(config or {}, ensure_ascii=False),
                json.dumps(review or {}, ensure_ascii=False) if review is not None else None,
                active_val if mark_active else inactive_val,
                source,
            ),
        )

        stats_by_term = {s["term"]: s for s in stats if s.get("term")}
        rows: list[tuple] = []
        for rank, term in enumerate(terms, start=1):
            s = stats_by_term.get(term) or {}
            rows.append(
                (
                    build_id,
                    term,
                    int(s.get("df") or 0),
                    int(s.get("total_tf") or 0),
                    float(s.get("score") or 0.0),
                    rank,
                )
            )
        if rows:
            conn.executemany(
                """
                INSERT INTO vocab_terms (build_id, term, df, total_tf, score, rank)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        conn.commit()
        return build_id
    finally:
        if own:
            conn.close()


def upsert_vocab_terms(
    terms: list[dict[str, Any]],
    *,
    build_id: str | None = None,
    conn: LexiconConnection | None = None,
) -> int:
    """
    向指定 build（默认当前 active）增补/更新词条。
    terms 元素: {term, df?, total_tf?, score?, rank?}
    """
    own = conn is None
    if own:
        conn = connect_lexicon_db()
    assert conn is not None
    try:
        if not build_id:
            active = True if conn.backend == "postgres" else 1
            row = conn.fetchone_dict(
                "SELECT id FROM vocab_builds WHERE is_active = ? ORDER BY built_at DESC LIMIT 1",
                (active,),
            )
            if not row:
                raise ValueError("没有 active 词表，请先 build 或指定 build_id")
            build_id = row["id"]

        count = 0
        for item in terms:
            term = str(item.get("term") or "").strip()
            if not term:
                continue
            df = int(item.get("df") or 0)
            total_tf = int(item.get("total_tf") or 0)
            score = float(item.get("score") or 0.0)
            rank = int(item.get("rank") or 0)
            if conn.backend == "postgres":
                conn.execute(
                    """
                    INSERT INTO vocab_terms (build_id, term, df, total_tf, score, rank)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (build_id, term) DO UPDATE SET
                      df = EXCLUDED.df,
                      total_tf = EXCLUDED.total_tf,
                      score = EXCLUDED.score,
                      rank = EXCLUDED.rank
                    """,
                    (build_id, term, df, total_tf, score, rank),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO vocab_terms (build_id, term, df, total_tf, score, rank)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(build_id, term) DO UPDATE SET
                      df = excluded.df,
                      total_tf = excluded.total_tf,
                      score = excluded.score,
                      rank = excluded.rank
                    """,
                    (build_id, term, df, total_tf, score, rank),
                )
            count += 1

        # 同步 n_terms
        n = conn.fetchone_dict(
            "SELECT COUNT(*) AS c FROM vocab_terms WHERE build_id = ?",
            (build_id,),
        )
        if n:
            conn.execute(
                "UPDATE vocab_builds SET n_terms = ? WHERE id = ?",
                (int(n["c"]), build_id),
            )
        conn.commit()
        return count
    finally:
        if own:
            conn.close()


def load_active_vocab_terms(conn: LexiconConnection | None = None) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = connect_lexicon_db()
    assert conn is not None
    try:
        active = True if conn.backend == "postgres" else 1
        build = conn.fetchone_dict(
            "SELECT id FROM vocab_builds WHERE is_active = ? ORDER BY built_at DESC LIMIT 1",
            (active,),
        )
        if not build:
            return []
        return conn.fetchall_dicts(
            """
            SELECT term, df, total_tf, score, rank
            FROM vocab_terms WHERE build_id = ?
            ORDER BY rank ASC, term ASC
            """,
            (build["id"],),
        )
    finally:
        if own:
            conn.close()


def replace_or_upsert_entities(
    records: list[dict[str, Any]],
    *,
    source: str = "extract",
    replace_extracted: bool = True,
    conn: LexiconConnection | None = None,
) -> int:
    """
    写入实体表。
    replace_extracted=True 时先删除 source='extract' 的旧行再写入（全量重建）；
    manual 增补的实体会保留。
    """
    own = conn is None
    if own:
        conn = connect_lexicon_db()
    assert conn is not None
    now = _now_iso()
    try:
        if replace_extracted and source == "extract":
            conn.execute("DELETE FROM entities WHERE source = ?", ("extract",))

        count = 0
        for item in records:
            name = str(item.get("name") or "").strip()
            etype = str(item.get("entity_type") or "").strip()
            if not name or etype not in {"person", "place", "org"}:
                continue
            df = int(item.get("df") or 0)
            total_tf = int(item.get("total_tf") or 0)
            if conn.backend == "postgres":
                conn.execute(
                    """
                    INSERT INTO entities (name, entity_type, df, total_tf, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (name, entity_type) DO UPDATE SET
                      df = EXCLUDED.df,
                      total_tf = EXCLUDED.total_tf,
                      source = EXCLUDED.source,
                      updated_at = EXCLUDED.updated_at
                    """,
                    (name, etype, df, total_tf, source, now),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO entities (name, entity_type, df, total_tf, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name, entity_type) DO UPDATE SET
                      df = excluded.df,
                      total_tf = excluded.total_tf,
                      source = excluded.source,
                      updated_at = excluded.updated_at
                    """,
                    (name, etype, df, total_tf, source, now),
                )
            count += 1
        conn.commit()
        return count
    finally:
        if own:
            conn.close()


def upsert_entities(
    records: list[dict[str, Any]],
    *,
    source: str = "manual",
    conn: LexiconConnection | None = None,
) -> int:
    """增补实体（不删除已有 extract 行）。"""
    return replace_or_upsert_entities(
        records,
        source=source,
        replace_extracted=False,
        conn=conn,
    )


def load_entities(
    *,
    entity_types: list[str] | None = None,
    conn: LexiconConnection | None = None,
) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = connect_lexicon_db()
    assert conn is not None
    try:
        if entity_types:
            placeholders = ",".join("?" * len(entity_types))
            return conn.fetchall_dicts(
                f"""
                SELECT name, entity_type, df, total_tf, source, updated_at
                FROM entities
                WHERE entity_type IN ({placeholders})
                ORDER BY entity_type, df DESC, name
                """,
                tuple(entity_types),
            )
        return conn.fetchall_dicts(
            """
            SELECT name, entity_type, df, total_tf, source, updated_at
            FROM entities
            ORDER BY entity_type, df DESC, name
            """
        )
    finally:
        if own:
            conn.close()


def sync_entities_json_to_db(
    path: Path | None = None,
    *,
    replace_extracted: bool = True,
    conn: LexiconConnection | None = None,
) -> int:
    path = path or resolve_path(
        (load_config().get("vocabulary") or {}).get("entities_output_path", "data/entities.json")
    )
    if not path.is_file():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    records = list(data.get("entities") or [])
    return replace_or_upsert_entities(
        records,
        source="extract",
        replace_extracted=replace_extracted,
        conn=conn,
    )


def upsert_stopwords(
    terms: list[str],
    *,
    source: str = "manual",
    comment: str = "",
    conn: LexiconConnection | None = None,
) -> list[str]:
    """增补停用词（已存在则更新 source/comment/updated_at）。返回本次涉及的词。"""
    cleaned = [t.strip() for t in terms if t and t.strip()]
    if not cleaned:
        return []

    own = conn is None
    if own:
        conn = connect_lexicon_db()
    assert conn is not None
    now = _now_iso()
    written: list[str] = []
    try:
        for term in cleaned:
            if conn.backend == "postgres":
                conn.execute(
                    """
                    INSERT INTO stopwords (term, source, comment, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (term) DO UPDATE SET
                      source = EXCLUDED.source,
                      comment = CASE
                        WHEN EXCLUDED.comment <> '' THEN EXCLUDED.comment
                        ELSE stopwords.comment
                      END,
                      updated_at = EXCLUDED.updated_at
                    """,
                    (term, source, comment, now, now),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO stopwords (term, source, comment, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(term) DO UPDATE SET
                      source = excluded.source,
                      comment = CASE
                        WHEN excluded.comment <> '' THEN excluded.comment
                        ELSE stopwords.comment
                      END,
                      updated_at = excluded.updated_at
                    """,
                    (term, source, comment, now, now),
                )
            written.append(term)
        conn.commit()
        return written
    finally:
        if own:
            conn.close()


def load_stopwords_from_db(conn: LexiconConnection | None = None) -> set[str]:
    own = conn is None
    if own:
        conn = connect_lexicon_db()
    assert conn is not None
    try:
        rows = conn.fetchall_dicts("SELECT term FROM stopwords")
        return {r["term"] for r in rows if r.get("term")}
    finally:
        if own:
            conn.close()


def sync_stopwords_file_to_db(
    path: Path | None = None,
    *,
    source: str = "file_seed",
    conn: LexiconConnection | None = None,
) -> int:
    """把 stopwords_zh.txt 导入数据库（增补，不删库内已有）。"""
    from src.tokenize import get_stopwords_path

    path = path or get_stopwords_path()
    if not path.is_file():
        return 0
    terms: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if w and not w.startswith("#"):
            terms.append(w)
    return len(upsert_stopwords(terms, source=source, comment="from file", conn=conn))


def export_stopwords_db_to_file(
    path: Path | None = None,
    *,
    conn: LexiconConnection | None = None,
) -> Path:
    """把数据库停用词导出为 txt（覆盖写，带来源注释分组）。"""
    from src.tokenize import get_stopwords_path

    path = path or get_stopwords_path()
    own = conn is None
    if own:
        conn = connect_lexicon_db()
    assert conn is not None
    try:
        rows = conn.fetchall_dicts(
            "SELECT term, source, comment FROM stopwords ORDER BY source, term"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# 由 lexicon DB 导出；可用 scripts/sync_lexicon_db.py 双向同步", ""]
        current_source = None
        for r in rows:
            src = r.get("source") or "manual"
            if src != current_source:
                current_source = src
                lines.append(f"# source={src}")
            lines.append(r["term"])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    finally:
        if own:
            conn.close()


def sync_vocabulary_json_to_db(
    path: Path | None = None,
    *,
    mark_active: bool = True,
    conn: LexiconConnection | None = None,
) -> str:
    """把 vocabulary.json 导入为一次 build。"""
    from src.vocabulary import resolve_vocabulary_config

    cfg = resolve_vocabulary_config()
    path = path or resolve_path(cfg.output_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return save_vocab_build(
        terms=list(data.get("terms") or []),
        stats=list(data.get("stats") or []),
        n_docs=int(data.get("n_docs") or 0),
        n_candidates=int(data.get("n_candidates") or 0),
        config=data.get("config"),
        review=data.get("review"),
        built_at=data.get("built_at"),
        source="import_json",
        mark_active=mark_active,
        conn=conn,
    )


def describe_lexicon_db(conn: LexiconConnection | None = None) -> dict[str, Any]:
    cfg = resolve_lexicon_db_config()
    own = conn is None
    if own:
        conn = connect_lexicon_db(cfg)
    assert conn is not None
    try:
        n_builds = conn.fetchone_dict("SELECT COUNT(*) AS c FROM vocab_builds")
        n_terms = conn.fetchone_dict("SELECT COUNT(*) AS c FROM vocab_terms")
        n_sw = conn.fetchone_dict("SELECT COUNT(*) AS c FROM stopwords")
        n_ent = conn.fetchone_dict("SELECT COUNT(*) AS c FROM entities")
        by_type_rows = conn.fetchall_dicts(
            "SELECT entity_type, COUNT(*) AS c FROM entities GROUP BY entity_type"
        )
        active = True if conn.backend == "postgres" else 1
        active_build = conn.fetchone_dict(
            "SELECT id, built_at, n_terms FROM vocab_builds WHERE is_active = ? LIMIT 1",
            (active,),
        )
        return {
            "backend": cfg.backend,
            "sqlite_path": str(resolve_path(cfg.sqlite_path)) if cfg.backend == "sqlite" else None,
            "database_url_set": bool(cfg.database_url),
            "vocab_builds": int(n_builds["c"]) if n_builds else 0,
            "vocab_terms": int(n_terms["c"]) if n_terms else 0,
            "stopwords": int(n_sw["c"]) if n_sw else 0,
            "entities": int(n_ent["c"]) if n_ent else 0,
            "entities_by_type": {r["entity_type"]: int(r["c"]) for r in by_type_rows},
            "active_build": active_build,
            "project_root": str(ROOT),
        }
    finally:
        if own:
            conn.close()
