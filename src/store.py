"""存储层：配置加载、SQLite 封装。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(relative: str) -> Path:
    """相对 My_rag 根目录解析路径；若已是绝对路径则原样返回。"""
    p = Path(relative)
    return p if p.is_absolute() else ROOT / relative


def resolve_diary_dir() -> Path:
    """日记目录：优先 .env 的 DIARY_DIR，否则 config.yaml 的 data.diary_dir。"""
    override = os.getenv("DIARY_DIR", "").strip()
    if override:
        return resolve_path(override)
    cfg = load_config()
    return resolve_path(cfg["data"]["diary_dir"])


def get_db() -> sqlite3.Connection:
    cfg = load_config()
    db_path = resolve_path(cfg["data"]["db_path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            text TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            source_file TEXT,
            word_count INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_date ON chunks(date);

        CREATE TABLE IF NOT EXISTS chunk_tags (
            chunk_id TEXT PRIMARY KEY REFERENCES chunks(id),
            topics TEXT,
            activities TEXT,
            emotions TEXT,
            food_mentions TEXT,
            people TEXT,
            is_touching_moment INTEGER DEFAULT 0,
            touching_summary TEXT,
            keywords TEXT,
            tag_method TEXT,
            extracted_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS chunk_term (
            term TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (term, chunk_id),
            FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_term_term ON chunk_term(term);
        CREATE INDEX IF NOT EXISTS idx_chunk_term_chunk ON chunk_term(chunk_id);

        CREATE TABLE IF NOT EXISTS chunk_entity (
            chunk_id TEXT NOT NULL,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            tf INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (chunk_id, name, entity_type),
            FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_entity_name ON chunk_entity(name);
        CREATE INDEX IF NOT EXISTS idx_chunk_entity_type ON chunk_entity(entity_type);

        CREATE TABLE IF NOT EXISTS ingest_log (
            source_file TEXT PRIMARY KEY,
            file_hash TEXT,
            chunk_count INTEGER,
            processed_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS memory_views (
            id TEXT PRIMARY KEY,
            chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            view_type TEXT NOT NULL,
            content TEXT NOT NULL,
            date TEXT NOT NULL,
            source_file TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            model_version TEXT DEFAULT 'v0.3'
        );
        CREATE INDEX IF NOT EXISTS idx_memory_views_chunk ON memory_views(chunk_id);
        CREATE INDEX IF NOT EXISTS idx_memory_views_type ON memory_views(view_type);
        CREATE INDEX IF NOT EXISTS idx_memory_views_date ON memory_views(date);
        """
    )
    # 兼容旧库：补列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunk_tags)").fetchall()}
    if "keywords" not in cols:
        conn.execute("ALTER TABLE chunk_tags ADD COLUMN keywords TEXT")
    if "tag_method" not in cols:
        conn.execute("ALTER TABLE chunk_tags ADD COLUMN tag_method TEXT")
    if "entities" not in cols:
        conn.execute("ALTER TABLE chunk_tags ADD COLUMN entities TEXT")
    conn.commit()


def save_chunks(chunks: list, conn: sqlite3.Connection) -> None:
    for c in chunks:
        conn.execute(
            """INSERT OR REPLACE INTO chunks
               (id, date, text, chunk_index, source_file, word_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (c.id, c.date, c.text, c.chunk_index, c.source_file, c.word_count),
        )
    conn.commit()


def delete_chunks_by_source(source_file: str, conn: sqlite3.Connection) -> None:
    """文件变更重导入前，清理旧 chunk 与标签。"""
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_file = ?", (source_file,)
        ).fetchall()
    ]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM chunk_entity WHERE chunk_id IN ({placeholders})", ids
    )
    conn.execute(
        f"DELETE FROM chunk_term WHERE chunk_id IN ({placeholders})", ids
    )
    conn.execute(
        f"DELETE FROM chunk_tags WHERE chunk_id IN ({placeholders})", ids
    )
    conn.execute(
        f"DELETE FROM memory_views WHERE chunk_id IN ({placeholders})", ids
    )
    conn.execute(
        f"DELETE FROM chunks WHERE id IN ({placeholders})", ids
    )
    conn.commit()
