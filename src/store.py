"""存储层：配置加载、SQLite 封装。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(relative: str) -> Path:
    return ROOT / relative


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
            extracted_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ingest_log (
            source_file TEXT PRIMARY KEY,
            file_hash TEXT,
            chunk_count INTEGER,
            processed_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
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
        f"DELETE FROM chunk_tags WHERE chunk_id IN ({placeholders})", ids
    )
    conn.execute(
        f"DELETE FROM chunks WHERE id IN ({placeholders})", ids
    )
    conn.commit()
