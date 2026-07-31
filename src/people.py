"""探索页人物：头像照片落盘 + 映射到「人物」系统文件夹下的 user_tag。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from src.store import get_db, load_config, resolve_path
from src.user_tags import (
    create_tag,
    ensure_system_folders,
    list_chunks_for_tag,
    random_tag_color,
)

_ALLOWED_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _cfg() -> dict[str, Any]:
    return load_config().get("people") or {}


def photos_root() -> Path:
    rel = str(_cfg().get("root") or "data/people")
    path = resolve_path(rel)
    path.mkdir(parents=True, exist_ok=True)
    return path


def max_upload_bytes() -> int:
    return int(_cfg().get("max_bytes") or 12 * 1024 * 1024)


def _new_id() -> str:
    return f"person_{uuid.uuid4().hex[:12]}"


def _ext_for(name: str, content_type: str | None) -> str:
    suffix = Path(name or "").suffix.lower()
    if suffix in _ALLOWED_EXT:
        return suffix
    ct = (content_type or "").lower().split(";")[0].strip()
    for ext, mime in _ALLOWED_EXT.items():
        if mime == ct:
            return ext
    raise ValueError("仅支持 jpg/png/gif/webp/bmp 图片")


def _mime_for(filename: str) -> str:
    return _ALLOWED_EXT.get(Path(filename).suffix.lower(), "application/octet-stream")


def _row_person(r, *, bind_count: int | None = None) -> dict[str, Any]:
    pid = r["id"]
    photo = (r["photo_filename"] or "").strip()
    out = {
        "id": pid,
        "name": r["name"],
        "tag_id": r["tag_id"],
        "photo_filename": photo or None,
        "photo_url": f"/api/people/{pid}/photo" if photo else None,
        "sort_order": int(r["sort_order"] or 0),
        "created_at": r["created_at"] or "",
        "tag_color": r["tag_color"] if "tag_color" in r.keys() else None,
    }
    if bind_count is not None:
        out["bind_count"] = bind_count
    return out


def list_people() -> list[dict[str, Any]]:
    ensure_system_folders()
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT p.*,
                   t.color AS tag_color,
                   (SELECT COUNT(*) FROM chunk_user_tags c WHERE c.tag_id = p.tag_id)
                     AS bind_count
            FROM people p
            LEFT JOIN user_tags t ON t.id = p.tag_id
            ORDER BY p.sort_order ASC, p.name ASC, p.id ASC
            """
        ).fetchall()
        return [
            _row_person(r, bind_count=int(r["bind_count"] or 0)) for r in rows
        ]
    finally:
        conn.close()


def get_person(person_id: str) -> dict[str, Any]:
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT p.*,
                   t.color AS tag_color,
                   (SELECT COUNT(*) FROM chunk_user_tags c WHERE c.tag_id = p.tag_id)
                     AS bind_count
            FROM people p
            LEFT JOIN user_tags t ON t.id = p.tag_id
            WHERE p.id = ?
            """,
            (person_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"人物不存在: {person_id}")
        return _row_person(row, bind_count=int(row["bind_count"] or 0))
    finally:
        conn.close()


def create_person(
    name: str,
    *,
    photo_data: bytes | None = None,
    original_name: str = "",
    content_type: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    n = (name or "").strip()
    if not n:
        raise ValueError("姓名不能为空")
    if len(n) > 64:
        raise ValueError("姓名过长")

    folder = ensure_system_folders()
    folder_id = folder["id"]
    tag = create_tag(n, folder_id=folder_id, color=color or random_tag_color())

    pid = _new_id()
    photo_filename = None
    if photo_data:
        if len(photo_data) > max_upload_bytes():
            raise ValueError("图片过大")
        ext = _ext_for(original_name, content_type)
        photo_filename = f"{pid}{ext}"
        (photos_root() / photo_filename).write_bytes(photo_data)

    conn = get_db()
    try:
        max_ord = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM people"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO people (id, name, tag_id, photo_filename, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pid, n, tag["id"], photo_filename, int(max_ord) + 1),
        )
        conn.commit()
    except Exception:
        # 回滚已写入的 tag / 照片
        if photo_filename:
            try:
                (photos_root() / photo_filename).unlink(missing_ok=True)
            except OSError:
                pass
        from src.user_tags import delete_tag

        try:
            delete_tag(tag["id"])
        except Exception:
            pass
        raise
    finally:
        conn.close()

    return get_person(pid)


def update_person(
    person_id: str,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"人物不存在: {person_id}")
        new_name = row["name"]
        if name is not None:
            n = name.strip()
            if not n:
                raise ValueError("姓名不能为空")
            if len(n) > 64:
                raise ValueError("姓名过长")
            new_name = n
            conn.execute(
                "UPDATE people SET name = ? WHERE id = ?",
                (new_name, person_id),
            )
            conn.execute(
                "UPDATE user_tags SET name = ? WHERE id = ?",
                (new_name, row["tag_id"]),
            )
            conn.commit()
    finally:
        conn.close()
    return get_person(person_id)


def save_person_photo(
    person_id: str,
    *,
    data: bytes,
    original_name: str = "",
    content_type: str | None = None,
) -> dict[str, Any]:
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > max_upload_bytes():
        raise ValueError("图片过大")
    ext = _ext_for(original_name, content_type)

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"人物不存在: {person_id}")
        old = (row["photo_filename"] or "").strip()
        filename = f"{person_id}{ext}"
        dest = photos_root() / filename
        dest.write_bytes(data)
        if old and old != filename:
            try:
                (photos_root() / old).unlink(missing_ok=True)
            except OSError:
                pass
        conn.execute(
            "UPDATE people SET photo_filename = ? WHERE id = ?",
            (filename, person_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_person(person_id)


def resolve_photo_file(person_id: str) -> tuple[Path, str]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT photo_filename FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"人物不存在: {person_id}")
        filename = (row["photo_filename"] or "").strip()
        if not filename:
            raise FileNotFoundError("尚未上传照片")
        path = photos_root() / filename
        if not path.is_file():
            raise FileNotFoundError("照片文件缺失")
        return path, _mime_for(filename)
    finally:
        conn.close()


def delete_person(person_id: str) -> dict[str, Any]:
    """删除人物、照片，并删除绑定的 tag（级联解绑 chunk）。"""
    from src.user_tags import delete_tag

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"人物不存在: {person_id}")
        tag_id = row["tag_id"]
        photo = (row["photo_filename"] or "").strip()
        conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
        conn.commit()
    finally:
        conn.close()

    if photo:
        try:
            (photos_root() / photo).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        delete_tag(tag_id)
    except ValueError:
        pass
    return {"ok": True, "id": person_id}


def person_chunks(person_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    person = get_person(person_id)
    return list_chunks_for_tag(person["tag_id"], limit=limit)


def cleanup_photo_for_tag(tag_id: str) -> None:
    """tag 被删除时清理对应人物照片（people 行由 ON DELETE CASCADE 清除）。"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT photo_filename FROM people WHERE tag_id = ?", (tag_id,)
        ).fetchone()
        if not row:
            return
        photo = (row["photo_filename"] or "").strip()
    finally:
        conn.close()
    if photo:
        try:
            (photos_root() / photo).unlink(missing_ok=True)
        except OSError:
            pass


def people_folder_meta() -> dict[str, Any]:
    return ensure_system_folders()
