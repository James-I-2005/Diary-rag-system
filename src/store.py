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
    return p if p.is_absolute() else (ROOT / relative).resolve()


def resolve_diary_dir() -> Path:
    """
    日记原文根目录（extract / ingest 共用），不必位于 data/ 下。

    优先级：
    1. config.yaml → extract.root（推荐：在此写任意绝对/相对路径）
    2. 环境变量 DIARY_DIR（未设 extract.root 时的覆盖）
    3. config.yaml → data.diary_dir
    """
    cfg = load_config()
    extract_root = str(((cfg.get("extract") or {}).get("root") or "")).strip()
    if extract_root:
        return resolve_path(extract_root)
    override = os.getenv("DIARY_DIR", "").strip()
    if override:
        return resolve_path(override)
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

        CREATE TABLE IF NOT EXISTS rag_sentences (
            id TEXT PRIMARY KEY,
            chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            sent_index INTEGER NOT NULL,
            date TEXT NOT NULL,
            source_file TEXT,
            model_version TEXT DEFAULT 'rag-sentence-v1',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_rag_sentences_chunk ON rag_sentences(chunk_id);
        CREATE INDEX IF NOT EXISTS idx_rag_sentences_date ON rag_sentences(date);

        CREATE TABLE IF NOT EXISTS day_images (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            filename TEXT NOT NULL,
            original_name TEXT,
            mime TEXT,
            size_bytes INTEGER DEFAULT 0,
            sort_index INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_day_images_date ON day_images(date, sort_index, created_at);

        CREATE TABLE IF NOT EXISTS tag_folders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT REFERENCES tag_folders(id) ON DELETE SET NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            system_key TEXT,
            locked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tag_folders_parent ON tag_folders(parent_id, sort_order);

        CREATE TABLE IF NOT EXISTS user_tags (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            folder_id TEXT REFERENCES tag_folders(id) ON DELETE SET NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            use_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_user_tags_folder ON user_tags(folder_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_user_tags_recent ON user_tags(last_used_at DESC);
        CREATE INDEX IF NOT EXISTS idx_user_tags_frequent ON user_tags(use_count DESC);

        CREATE TABLE IF NOT EXISTS chunk_user_tags (
            chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL REFERENCES user_tags(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (chunk_id, tag_id)
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_user_tags_tag ON chunk_user_tags(tag_id);

        CREATE TABLE IF NOT EXISTS people (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tag_id TEXT NOT NULL UNIQUE REFERENCES user_tags(id) ON DELETE CASCADE,
            photo_filename TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_people_sort ON people(sort_order, name, id);

        CREATE TABLE IF NOT EXISTS places (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tag_id TEXT NOT NULL UNIQUE REFERENCES user_tags(id) ON DELETE CASCADE,
            photo_filename TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_places_sort ON places(sort_order, name, id);
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

    folder_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(tag_folders)").fetchall()
    }
    if "system_key" not in folder_cols:
        conn.execute("ALTER TABLE tag_folders ADD COLUMN system_key TEXT")
    if "locked" not in folder_cols:
        conn.execute(
            "ALTER TABLE tag_folders ADD COLUMN locked INTEGER NOT NULL DEFAULT 0"
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
    """文件变更重导入前，清理旧 chunk / 标签 / rag_sentences。"""
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE source_file = ?", (source_file,)
        ).fetchall()
    ]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    n_sent = conn.execute(
        f"SELECT COUNT(*) FROM rag_sentences WHERE chunk_id IN ({placeholders})",
        ids,
    ).fetchone()[0]
    if n_sent:
        print(
            f"  [warn] 重导入 {source_file!r}：将删除 {len(ids)} 个 chunk "
            f"及关联的 {n_sent} 条 rag_sentence（需重新 python main.py sentences）"
        )
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
        f"DELETE FROM chunk_user_tags WHERE chunk_id IN ({placeholders})", ids
    )
    conn.execute(
        f"DELETE FROM memory_views WHERE chunk_id IN ({placeholders})", ids
    )
    conn.execute(
        f"DELETE FROM rag_sentences WHERE chunk_id IN ({placeholders})", ids
    )
    conn.execute(
        f"DELETE FROM chunks WHERE id IN ({placeholders})", ids
    )
    conn.commit()
