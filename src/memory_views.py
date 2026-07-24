"""Memory View 存储：SQLite + Chroma 同步。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.store import get_db, load_config

VALID_VIEW_TYPES: frozenset[str] = frozenset(
    {"event", "narrative", "growth", "identity", "future_query"}
)


@dataclass
class MemoryViewRecord:
    id: str
    chunk_id: str
    view_type: str
    content: str
    date: str
    source_file: str = ""
    model_version: str = "v0.3"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chunk_id": self.chunk_id,
            "view_type": self.view_type,
            "content": self.content,
            "date": self.date,
            "source_file": self.source_file,
            "model_version": self.model_version,
        }


def _cfg() -> dict[str, Any]:
    return load_config().get("memory_views") or {}


def max_views_per_chunk() -> int:
    return int(_cfg().get("max_views_per_chunk", 6))


def model_version() -> str:
    return str(_cfg().get("model_version", "v0.3"))


def normalize_view_type(raw: str) -> str | None:
    key = str(raw or "").strip().lower().replace("-", "_")
    if key in VALID_VIEW_TYPES:
        return key
    aliases = {
        "events": "event",
        "story": "narrative",
        "grow": "growth",
        "ident": "identity",
        "future": "future_query",
        "query": "future_query",
    }
    return aliases.get(key)


def delete_views_for_chunk(chunk_id: str) -> list[str]:
    """删除 chunk 的全部 view，返回被删 view_id 列表（供 Chroma 同步）。"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id FROM memory_views WHERE chunk_id = ?", (chunk_id,)
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.execute("DELETE FROM memory_views WHERE chunk_id = ?", (chunk_id,))
            conn.commit()
        return ids
    finally:
        conn.close()


def save_views_for_chunk(
    chunk_id: str,
    views: list[dict[str, str]],
    *,
    date: str,
    source_file: str = "",
    model_ver: str | None = None,
) -> list[MemoryViewRecord]:
    """替换写入某 chunk 的 views。"""
    delete_views_for_chunk(chunk_id)
    ver = model_ver or model_version()
    records: list[MemoryViewRecord] = []
    conn = get_db()
    try:
        idx = 0
        for v in views[: max_views_per_chunk()]:
            vtype = normalize_view_type(v.get("type", ""))
            content = str(v.get("content") or "").strip()
            if not vtype or not content:
                continue
            vid = f"{chunk_id}_v{idx}"
            idx += 1
            rec = MemoryViewRecord(
                id=vid,
                chunk_id=chunk_id,
                view_type=vtype,
                content=content,
                date=date,
                source_file=source_file,
                model_version=ver,
            )
            conn.execute(
                """INSERT INTO memory_views
                   (id, chunk_id, view_type, content, date, source_file, model_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.id,
                    rec.chunk_id,
                    rec.view_type,
                    rec.content,
                    rec.date,
                    rec.source_file,
                    rec.model_version,
                ),
            )
            records.append(rec)
        conn.commit()
    finally:
        conn.close()
    return records


def list_chunks_without_views(limit: int | None = None) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        sql = """
            SELECT c.id, c.date, c.text, c.source_file
            FROM chunks c
            LEFT JOIN memory_views mv ON mv.chunk_id = c.id
            WHERE mv.id IS NULL
            ORDER BY c.date, c.id
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def list_all_chunks(limit: int | None = None) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT id, date, text, source_file FROM chunks ORDER BY date, id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def count_views() -> int:
    conn = get_db()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM memory_views").fetchone()[0])
    finally:
        conn.close()


def fetch_views_for_index(view_ids: list[str] | None = None) -> list[MemoryViewRecord]:
    conn = get_db()
    try:
        if view_ids:
            placeholders = ",".join("?" * len(view_ids))
            rows = conn.execute(
                f"""SELECT id, chunk_id, view_type, content, date, source_file, model_version
                    FROM memory_views WHERE id IN ({placeholders})""",
                view_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, chunk_id, view_type, content, date, source_file, model_version
                   FROM memory_views ORDER BY date, chunk_id, id"""
            ).fetchall()
        return [
            MemoryViewRecord(
                id=r["id"],
                chunk_id=r["chunk_id"],
                view_type=r["view_type"],
                content=r["content"],
                date=r["date"],
                source_file=r["source_file"] or "",
                model_version=r["model_version"] or "v0.3",
            )
            for r in rows
        ]
    finally:
        conn.close()
