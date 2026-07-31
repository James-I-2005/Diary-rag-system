"""日历日视图图片：文件落盘 + SQLite 元数据。

不把二进制塞进 DB（会迅速膨胀）；路径与排序信息进 SQLite，
原图放在 data/day_images/{date}/{id}.ext。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.store import get_db, load_config, resolve_path

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ALLOWED_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _cfg() -> dict[str, Any]:
    return load_config().get("day_images") or {}


def images_root() -> Path:
    rel = str(_cfg().get("root") or "data/day_images")
    path = resolve_path(rel)
    path.mkdir(parents=True, exist_ok=True)
    return path


def max_upload_bytes() -> int:
    # 默认 12MB
    return int(_cfg().get("max_bytes") or 12 * 1024 * 1024)


def ensure_day_images_table(conn=None) -> None:
    own = conn is None
    if own:
        conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS day_images (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT,
                mime TEXT,
                size_bytes INTEGER DEFAULT 0,
                sort_index INTEGER DEFAULT 0,
                created_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_day_images_date ON day_images(date, sort_index, created_at)"
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def _validate_date(day: str) -> str:
    s = (day or "").strip()
    if not _DATE_RE.fullmatch(s):
        raise ValueError(f"日期格式须为 YYYY-MM-DD，收到: {day}")
    return s


def _ext_for(name: str, content_type: str | None) -> str:
    suffix = Path(name or "").suffix.lower()
    if suffix in _ALLOWED_EXT:
        return suffix
    ct = (content_type or "").lower().split(";")[0].strip()
    for ext, mime in _ALLOWED_EXT.items():
        if mime == ct:
            return ext
    raise ValueError("仅支持 jpg/png/gif/webp/bmp 图片")


def list_images(day: str) -> list[dict[str, Any]]:
    d = _validate_date(day)
    conn = get_db()
    try:
        ensure_day_images_table(conn)
        rows = conn.execute(
            """
            SELECT id, date, filename, original_name, mime, size_bytes, sort_index, created_at
            FROM day_images
            WHERE date = ?
            ORDER BY sort_index ASC, created_at ASC, id ASC
            """,
            (d,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["url"] = f"/api/diary/days/{d}/images/{r['id']}/file"
            out.append(item)
        return out
    finally:
        conn.close()


def save_image(
    day: str,
    *,
    data: bytes,
    original_name: str = "",
    content_type: str | None = None,
) -> dict[str, Any]:
    d = _validate_date(day)
    if not data:
        raise ValueError("空文件")
    if len(data) > max_upload_bytes():
        raise ValueError(f"图片过大（上限 {max_upload_bytes() // (1024 * 1024)}MB）")

    ext = _ext_for(original_name, content_type)
    mime = _ALLOWED_EXT[ext]
    image_id = str(uuid.uuid4())
    filename = f"{image_id}{ext}"

    day_dir = images_root() / d
    day_dir.mkdir(parents=True, exist_ok=True)
    dest = day_dir / filename
    dest.write_bytes(data)

    conn = get_db()
    try:
        ensure_day_images_table(conn)
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_index), -1) AS m FROM day_images WHERE date = ?",
            (d,),
        ).fetchone()
        sort_index = int(row["m"] if row else -1) + 1
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO day_images
            (id, date, filename, original_name, mime, size_bytes, sort_index, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_id,
                d,
                filename,
                (original_name or filename)[:200],
                mime,
                len(data),
                sort_index,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": image_id,
        "date": d,
        "filename": filename,
        "original_name": original_name or filename,
        "mime": mime,
        "size_bytes": len(data),
        "sort_index": sort_index,
        "url": f"/api/diary/days/{d}/images/{image_id}/file",
    }


def resolve_image_file(day: str, image_id: str) -> tuple[Path, str]:
    d = _validate_date(day)
    iid = (image_id or "").strip()
    conn = get_db()
    try:
        ensure_day_images_table(conn)
        row = conn.execute(
            "SELECT filename, mime FROM day_images WHERE id = ? AND date = ?",
            (iid, d),
        ).fetchone()
        if not row:
            raise KeyError("图片不存在")
        path = images_root() / d / row["filename"]
        if not path.is_file():
            raise FileNotFoundError("图片文件缺失")
        return path, str(row["mime"] or "application/octet-stream")
    finally:
        conn.close()


def delete_image(day: str, image_id: str) -> bool:
    d = _validate_date(day)
    iid = (image_id or "").strip()
    conn = get_db()
    try:
        ensure_day_images_table(conn)
        row = conn.execute(
            "SELECT filename FROM day_images WHERE id = ? AND date = ?",
            (iid, d),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM day_images WHERE id = ? AND date = ?", (iid, d))
        conn.commit()
        path = images_root() / d / row["filename"]
        if path.is_file():
            path.unlink()
        return True
    finally:
        conn.close()
