"""探索页地点：照片落盘 + 映射到「地点」系统文件夹下的 user_tag。"""

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
    return load_config().get("places") or {}


def photos_root() -> Path:
    rel = str(_cfg().get("root") or "data/places")
    path = resolve_path(rel)
    path.mkdir(parents=True, exist_ok=True)
    return path


def max_upload_bytes() -> int:
    return int(_cfg().get("max_bytes") or 12 * 1024 * 1024)


def _new_id() -> str:
    return f"place_{uuid.uuid4().hex[:12]}"


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


def _row_place(r, *, bind_count: int | None = None) -> dict[str, Any]:
    pid = r["id"]
    photo = (r["photo_filename"] or "").strip()
    out = {
        "id": pid,
        "name": r["name"],
        "tag_id": r["tag_id"],
        "photo_filename": photo or None,
        "photo_url": f"/api/places/{pid}/photo" if photo else None,
        "sort_order": int(r["sort_order"] or 0),
        "created_at": r["created_at"] or "",
        "tag_color": r["tag_color"] if "tag_color" in r.keys() else None,
    }
    if bind_count is not None:
        out["bind_count"] = bind_count
    return out


def list_places() -> list[dict[str, Any]]:
    ensure_system_folders()["places"]
    reconcile_places_folder()
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT p.*,
                   t.color AS tag_color,
                   (SELECT COUNT(*) FROM chunk_user_tags c WHERE c.tag_id = p.tag_id)
                     AS bind_count
            FROM places p
            LEFT JOIN user_tags t ON t.id = p.tag_id
            ORDER BY p.sort_order ASC, p.name ASC, p.id ASC
            """
        ).fetchall()
        return [
            _row_place(r, bind_count=int(r["bind_count"] or 0)) for r in rows
        ]
    finally:
        conn.close()


def get_place_by_tag(tag_id: str) -> dict[str, Any] | None:
    tid = (tag_id or "").strip()
    if not tid:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT p.*,
                   t.color AS tag_color,
                   (SELECT COUNT(*) FROM chunk_user_tags c WHERE c.tag_id = p.tag_id)
                     AS bind_count
            FROM places p
            LEFT JOIN user_tags t ON t.id = p.tag_id
            WHERE p.tag_id = ?
            """,
            (tid,),
        ).fetchone()
        if not row:
            return None
        return _row_place(row, bind_count=int(row["bind_count"] or 0))
    finally:
        conn.close()


def ensure_place_for_tag(tag_id: str) -> dict[str, Any] | None:
    """若 tag 位于「地点」文件夹且尚无地点记录，则补建一条（幂等）。"""
    tid = (tag_id or "").strip()
    if not tid:
        return None
    folder = ensure_system_folders()["places"]
    places_fid = folder["id"]
    conn = get_db()
    try:
        tag = conn.execute(
            "SELECT id, name, folder_id FROM user_tags WHERE id = ?", (tid,)
        ).fetchone()
        if not tag:
            return None
        if tag["folder_id"] != places_fid:
            return None
        existing = conn.execute(
            "SELECT id FROM places WHERE tag_id = ?", (tid,)
        ).fetchone()
        if existing:
            return get_place(existing["id"])
        pid = _new_id()
        max_ord = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM places"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO places (id, name, tag_id, photo_filename, sort_order)
            VALUES (?, ?, ?, NULL, ?)
            """,
            (pid, tag["name"], tid, int(max_ord) + 1),
        )
        conn.commit()
        return get_place(pid)
    finally:
        conn.close()


def remove_place_for_tag(tag_id: str) -> bool:
    """tag 离开「地点」文件夹时：删除地点记录与照片，保留 tag。"""
    tid = (tag_id or "").strip()
    if not tid:
        return False
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, photo_filename FROM places WHERE tag_id = ?", (tid,)
        ).fetchone()
        if not row:
            return False
        photo = (row["photo_filename"] or "").strip()
        conn.execute("DELETE FROM places WHERE id = ?", (row["id"],))
        conn.commit()
    finally:
        conn.close()
    if photo:
        try:
            (photos_root() / photo).unlink(missing_ok=True)
        except OSError:
            pass
    return True


def sync_tag_place_link(tag_id: str) -> None:
    """按 tag 当前所属文件夹同步人物：在「地点」内则确保有记录，否则移除人物。"""
    tid = (tag_id or "").strip()
    if not tid:
        return
    folder = ensure_system_folders()["places"]
    places_fid = folder["id"]
    conn = get_db()
    try:
        tag = conn.execute(
            "SELECT folder_id FROM user_tags WHERE id = ?", (tid,)
        ).fetchone()
        if not tag:
            return
        in_people = tag["folder_id"] == places_fid
    finally:
        conn.close()
    if in_people:
        ensure_place_for_tag(tid)
    else:
        remove_place_for_tag(tid)


def reconcile_places_folder() -> None:
    """纠正漂移：人物文件夹内的 tag 都有人物；文件夹外的 tag 不挂人物。"""
    folder = ensure_system_folders()["places"]
    places_fid = folder["id"]
    conn = get_db()
    try:
        in_folder = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM user_tags WHERE folder_id = ?", (places_fid,)
            ).fetchall()
        ]
        orphan_people = [
            r["tag_id"]
            for r in conn.execute(
                """
                SELECT p.tag_id FROM places p
                LEFT JOIN user_tags t ON t.id = p.tag_id
                WHERE t.id IS NULL OR t.folder_id IS NULL OR t.folder_id != ?
                """,
                (places_fid,),
            ).fetchall()
        ]
    finally:
        conn.close()
    for tid in in_folder:
        ensure_place_for_tag(tid)
    for tid in orphan_people:
        remove_place_for_tag(tid)


def get_place(place_id: str) -> dict[str, Any]:
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT p.*,
                   t.color AS tag_color,
                   (SELECT COUNT(*) FROM chunk_user_tags c WHERE c.tag_id = p.tag_id)
                     AS bind_count
            FROM places p
            LEFT JOIN user_tags t ON t.id = p.tag_id
            WHERE p.id = ?
            """,
            (place_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"地点不存在: {place_id}")
        return _row_place(row, bind_count=int(row["bind_count"] or 0))
    finally:
        conn.close()


def create_place(
    name: str,
    *,
    photo_data: bytes | None = None,
    original_name: str = "",
    content_type: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    n = (name or "").strip()
    if not n:
        raise ValueError("名称不能为空")
    if len(n) > 64:
        raise ValueError("名称过长")

    folder = ensure_system_folders()["places"]
    folder_id = folder["id"]
    # create_tag 会同步创建 places 记录
    tag = create_tag(n, folder_id=folder_id, color=color or random_tag_color())
    place = get_place_by_tag(tag["id"])
    if not place:
        place = ensure_place_for_tag(tag["id"])
    if not place:
        raise RuntimeError("创建地点失败：未能同步到地点表")

    if photo_data:
        return save_place_photo(
            place["id"],
            data=photo_data,
            original_name=original_name,
            content_type=content_type,
        )
    return get_place(place["id"])


def update_place(
    place_id: str,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM places WHERE id = ?", (place_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"地点不存在: {place_id}")
        new_name = row["name"]
        if name is not None:
            n = name.strip()
            if not n:
                raise ValueError("名称不能为空")
            if len(n) > 64:
                raise ValueError("名称过长")
            new_name = n
            conn.execute(
                "UPDATE places SET name = ? WHERE id = ?",
                (new_name, place_id),
            )
            conn.execute(
                "UPDATE user_tags SET name = ? WHERE id = ?",
                (new_name, row["tag_id"]),
            )
            conn.commit()
    finally:
        conn.close()
    return get_place(place_id)


def save_place_photo(
    place_id: str,
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
            "SELECT * FROM places WHERE id = ?", (place_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"地点不存在: {place_id}")
        old = (row["photo_filename"] or "").strip()
        filename = f"{place_id}{ext}"
        dest = photos_root() / filename
        dest.write_bytes(data)
        if old and old != filename:
            try:
                (photos_root() / old).unlink(missing_ok=True)
            except OSError:
                pass
        conn.execute(
            "UPDATE places SET photo_filename = ? WHERE id = ?",
            (filename, place_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_place(place_id)


def resolve_photo_file(place_id: str) -> tuple[Path, str]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT photo_filename FROM places WHERE id = ?", (place_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"地点不存在: {place_id}")
        filename = (row["photo_filename"] or "").strip()
        if not filename:
            raise FileNotFoundError("尚未上传照片")
        path = photos_root() / filename
        if not path.is_file():
            raise FileNotFoundError("照片文件缺失")
        return path, _mime_for(filename)
    finally:
        conn.close()


def delete_place(place_id: str) -> dict[str, Any]:
    """删除人物、照片，并删除绑定的 tag（级联解绑 chunk）。"""
    from src.user_tags import delete_tag

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM places WHERE id = ?", (place_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"地点不存在: {place_id}")
        tag_id = row["tag_id"]
        photo = (row["photo_filename"] or "").strip()
        conn.execute("DELETE FROM places WHERE id = ?", (place_id,))
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
    return {"ok": True, "id": place_id}


def place_chunks(place_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    person = get_place(place_id)
    return list_chunks_for_tag(person["tag_id"], limit=limit)


def cleanup_photo_for_tag(tag_id: str) -> None:
    """tag 被删除时清理对应人物照片（people 行由 ON DELETE CASCADE 清除）。"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT photo_filename FROM places WHERE tag_id = ?", (tag_id,)
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


def places_folder_meta() -> dict[str, Any]:
    return ensure_system_folders()["places"]
