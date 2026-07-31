"""用户手动 Tag：目录式文件夹 + chunk 多对多绑定。"""

from __future__ import annotations

import random
import re
import uuid
from typing import Any

from src.store import get_db

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# 系统文件夹：人物（不可移动 / 删除）
PEOPLE_FOLDER_SYSTEM_KEY = "people"
PEOPLE_FOLDER_ID = "folder_system_people"
PEOPLE_FOLDER_NAME = "人物"

# 预定色板（创建 / 改色只能从中选）
PRESET_COLORS: list[str] = [
    "#c45c48",
    "#d4893a",
    "#c9a227",
    "#5a9a6a",
    "#3d8b8b",
    "#3a6ea5",
    "#5c6bc0",
    "#7a5ea7",
    "#b85c8a",
    "#8b6f5c",
    "#6b7280",
    "#4a5568",
]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def list_preset_colors() -> list[str]:
    return list(PRESET_COLORS)


def random_tag_color() -> str:
    return random.choice(PRESET_COLORS)


def _norm_color(color: str | None) -> str:
    c = (color or "").strip().lower()
    if c in PRESET_COLORS:
        return c
    if c and _COLOR_RE.fullmatch(c):
        # 旧数据或非法输入：吸附到色板最近色（按 RGB 距离）
        return _nearest_preset(c)
    return random_tag_color()


def _nearest_preset(hex_color: str) -> str:
    def rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    tr, tg, tb = rgb(hex_color)
    best = PRESET_COLORS[0]
    best_d = 1e18
    for p in PRESET_COLORS:
        r, g, b = rgb(p)
        d = (r - tr) ** 2 + (g - tg) ** 2 + (b - tb) ** 2
        if d < best_d:
            best_d = d
            best = p
    return best


def require_preset_color(color: str) -> str:
    c = (color or "").strip().lower()
    if c not in PRESET_COLORS:
        raise ValueError("颜色须为预定色板中的色值")
    return c


def _row_folder(r) -> dict[str, Any]:
    keys = r.keys()
    locked = int(r["locked"] or 0) if "locked" in keys else 0
    system_key = ""
    if "system_key" in keys:
        system_key = (r["system_key"] or "").strip()
    return {
        "id": r["id"],
        "name": r["name"],
        "parent_id": r["parent_id"],
        "sort_order": int(r["sort_order"] or 0),
        "system_key": system_key or None,
        "locked": bool(locked),
        "created_at": r["created_at"] or "",
    }


def ensure_system_folders() -> dict[str, Any]:
    """确保根目录存在锁定的「人物」文件夹；返回该文件夹。"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM tag_folders WHERE system_key = ?",
            (PEOPLE_FOLDER_SYSTEM_KEY,),
        ).fetchone()
        if row:
            # 纠偏：锁定且保持在根目录
            if not int(row["locked"] or 0) or row["parent_id"] is not None:
                conn.execute(
                    """
                    UPDATE tag_folders
                    SET locked = 1, parent_id = NULL
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM tag_folders WHERE id = ?", (row["id"],)
                ).fetchone()
            return _row_folder(row)

        # 兼容：同 id 已存在但无 system_key
        existing = conn.execute(
            "SELECT * FROM tag_folders WHERE id = ?", (PEOPLE_FOLDER_ID,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE tag_folders
                SET system_key = ?, locked = 1, parent_id = NULL, name = ?
                WHERE id = ?
                """,
                (PEOPLE_FOLDER_SYSTEM_KEY, PEOPLE_FOLDER_NAME, PEOPLE_FOLDER_ID),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM tag_folders WHERE id = ?", (PEOPLE_FOLDER_ID,)
            ).fetchone()
            return _row_folder(row)

        min_ord = conn.execute(
            """
            SELECT COALESCE(MIN(sort_order), 1) FROM tag_folders
            WHERE parent_id IS NULL
            """
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO tag_folders (id, name, parent_id, sort_order, system_key, locked)
            VALUES (?, ?, NULL, ?, ?, 1)
            """,
            (
                PEOPLE_FOLDER_ID,
                PEOPLE_FOLDER_NAME,
                int(min_ord) - 1,
                PEOPLE_FOLDER_SYSTEM_KEY,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM tag_folders WHERE id = ?", (PEOPLE_FOLDER_ID,)
        ).fetchone()
        return _row_folder(row)
    finally:
        conn.close()


def _folder_is_locked(conn, folder_id: str) -> bool:
    row = conn.execute(
        "SELECT locked, system_key FROM tag_folders WHERE id = ?", (folder_id,)
    ).fetchone()
    if not row:
        return False
    if int(row["locked"] or 0):
        return True
    return bool((row["system_key"] or "").strip())


def _row_tag(r, *, bind_count: int | None = None) -> dict[str, Any]:
    out = {
        "id": r["id"],
        "name": r["name"],
        "color": r["color"],
        "folder_id": r["folder_id"],
        "sort_order": int(r["sort_order"] or 0),
        "last_used_at": r["last_used_at"] or "",
        "use_count": int(r["use_count"] or 0),
        "created_at": r["created_at"] or "",
    }
    if bind_count is not None:
        out["bind_count"] = bind_count
    return out


class UserTag:
    """用户手动 Tag 领域对象（对应 user_tags 表一行）。"""

    __slots__ = (
        "id",
        "name",
        "color",
        "folder_id",
        "sort_order",
        "last_used_at",
        "use_count",
        "created_at",
        "bind_count",
    )

    def __init__(
        self,
        *,
        id: str,
        name: str,
        color: str = "#6b7280",
        folder_id: str | None = None,
        sort_order: int = 0,
        last_used_at: str = "",
        use_count: int = 0,
        created_at: str = "",
        bind_count: int | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.color = color
        self.folder_id = folder_id
        self.sort_order = int(sort_order or 0)
        self.last_used_at = last_used_at or ""
        self.use_count = int(use_count or 0)
        self.created_at = created_at or ""
        self.bind_count = bind_count

    @classmethod
    def from_row(cls, r: Any, *, bind_count: int | None = None) -> UserTag:
        data = _row_tag(r, bind_count=bind_count)
        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserTag:
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or ""),
            color=str(data.get("color") or "#6b7280"),
            folder_id=data.get("folder_id"),
            sort_order=int(data.get("sort_order") or 0),
            last_used_at=str(data.get("last_used_at") or ""),
            use_count=int(data.get("use_count") or 0),
            created_at=str(data.get("created_at") or ""),
            bind_count=data.get("bind_count"),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "folder_id": self.folder_id,
            "sort_order": self.sort_order,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "created_at": self.created_at,
        }
        if self.bind_count is not None:
            out["bind_count"] = self.bind_count
        return out

    @classmethod
    def create(
        cls,
        name: str,
        *,
        folder_id: str | None = None,
        color: str | None = None,
    ) -> UserTag:
        """创建 tag 并返回实例。"""
        return cls.from_dict(
            create_tag(name, folder_id=folder_id, color=color)
        )

    def bind(self, chunk_ids: list[str]) -> dict[str, Any]:
        """将若干 chunk 绑定到本 tag。"""
        return bind_chunks(self.id, chunk_ids)

    def update(
        self,
        *,
        name: str | None = None,
        color: str | None = None,
        folder_id: str | None = None,
        clear_folder: bool = False,
        sort_order: int | None = None,
    ) -> UserTag:
        data = update_tag(
            self.id,
            name=name,
            color=color,
            folder_id=folder_id,
            clear_folder=clear_folder,
            sort_order=sort_order,
        )
        refreshed = UserTag.from_dict(data)
        self.name = refreshed.name
        self.color = refreshed.color
        self.folder_id = refreshed.folder_id
        self.sort_order = refreshed.sort_order
        self.last_used_at = refreshed.last_used_at
        self.use_count = refreshed.use_count
        self.created_at = refreshed.created_at
        self.bind_count = refreshed.bind_count
        return self

    def delete(self) -> dict[str, Any]:
        return delete_tag(self.id)

    def list_chunks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return list_chunks_for_tag(self.id, limit=limit)


def _folder_exists(conn, folder_id: str | None) -> bool:
    if folder_id is None:
        return True
    row = conn.execute(
        "SELECT 1 FROM tag_folders WHERE id = ?", (folder_id,)
    ).fetchone()
    return row is not None


def _would_cycle(conn, folder_id: str, new_parent_id: str | None) -> bool:
    """把 folder 移到 new_parent 是否形成环。"""
    if new_parent_id is None:
        return False
    if new_parent_id == folder_id:
        return True
    cur = new_parent_id
    seen = {folder_id}
    while cur:
        if cur in seen:
            return True
        seen.add(cur)
        row = conn.execute(
            "SELECT parent_id FROM tag_folders WHERE id = ?", (cur,)
        ).fetchone()
        if not row:
            break
        cur = row["parent_id"]
    return False


def list_tree(*, folder_id: str | None = None) -> dict[str, Any]:
    """当前目录：子文件夹 + 本层 tag。folder_id=None 表示根。"""
    ensure_system_folders()
    conn = get_db()
    try:
        if folder_id is not None and not _folder_exists(conn, folder_id):
            raise ValueError(f"文件夹不存在: {folder_id}")

        if folder_id is None:
            folders = conn.execute(
                """
                SELECT * FROM tag_folders
                WHERE parent_id IS NULL
                ORDER BY sort_order, name, id
                """
            ).fetchall()
            tags = conn.execute(
                """
                SELECT t.*,
                       (SELECT COUNT(*) FROM chunk_user_tags c WHERE c.tag_id = t.id) AS bind_count
                FROM user_tags t
                WHERE t.folder_id IS NULL
                ORDER BY t.sort_order, t.name, t.id
                """
            ).fetchall()
        else:
            folders = conn.execute(
                """
                SELECT * FROM tag_folders
                WHERE parent_id = ?
                ORDER BY sort_order, name, id
                """,
                (folder_id,),
            ).fetchall()
            tags = conn.execute(
                """
                SELECT t.*,
                       (SELECT COUNT(*) FROM chunk_user_tags c WHERE c.tag_id = t.id) AS bind_count
                FROM user_tags t
                WHERE t.folder_id = ?
                ORDER BY t.sort_order, t.name, t.id
                """,
                (folder_id,),
            ).fetchall()

        breadcrumb = _breadcrumb(conn, folder_id)
        return {
            "folder_id": folder_id,
            "breadcrumb": breadcrumb,
            "folders": [_row_folder(r) for r in folders],
            "tags": [_row_tag(r, bind_count=int(r["bind_count"] or 0)) for r in tags],
        }
    finally:
        conn.close()


def list_folders_flat() -> list[dict[str, Any]]:
    """全部文件夹扁平列表（含路径），供移动目标选择。"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM tag_folders ORDER BY sort_order, name, id"
        ).fetchall()
        by_id = {r["id"]: r for r in rows}

        def path_of(fid: str) -> str:
            parts: list[str] = []
            cur = fid
            guard = 0
            while cur and guard < 64:
                row = by_id.get(cur)
                if not row:
                    break
                parts.append(row["name"])
                cur = row["parent_id"]
                guard += 1
            parts.reverse()
            return " / ".join(parts) if parts else ""

        out = [{"id": None, "name": "根目录", "path": "根目录", "parent_id": None}]
        for r in rows:
            keys = r.keys()
            locked = bool(int(r["locked"] or 0)) if "locked" in keys else False
            system_key = None
            if "system_key" in keys:
                system_key = (r["system_key"] or "").strip() or None
            out.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "path": path_of(r["id"]),
                    "parent_id": r["parent_id"],
                    "locked": locked,
                    "system_key": system_key,
                }
            )
        return out
    finally:
        conn.close()


def _breadcrumb(conn, folder_id: str | None) -> list[dict[str, Any]]:
    if folder_id is None:
        return [{"id": None, "name": "根目录"}]
    chain: list[dict[str, Any]] = []
    cur = folder_id
    guard = 0
    while cur and guard < 64:
        row = conn.execute(
            "SELECT id, name, parent_id FROM tag_folders WHERE id = ?", (cur,)
        ).fetchone()
        if not row:
            break
        chain.append({"id": row["id"], "name": row["name"]})
        cur = row["parent_id"]
        guard += 1
    chain.reverse()
    return [{"id": None, "name": "根目录"}, *chain]


def list_recent(*, limit: int = 4) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 40))
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM user_tags
            WHERE last_used_at IS NOT NULL AND last_used_at != ''
            ORDER BY last_used_at DESC, use_count DESC, name
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        if len(rows) < lim:
            # 从未使用过的按创建时间补齐
            have = {r["id"] for r in rows}
            extra = conn.execute(
                """
                SELECT * FROM user_tags
                ORDER BY created_at DESC, name
                LIMIT ?
                """,
                (lim * 2,),
            ).fetchall()
            for r in extra:
                if r["id"] in have:
                    continue
                rows.append(r)
                have.add(r["id"])
                if len(rows) >= lim:
                    break
        return [_row_tag(r) for r in rows[:lim]]
    finally:
        conn.close()


def list_frequent(*, limit: int = 12) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 100))
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT t.*,
                   (SELECT COUNT(*) FROM chunk_user_tags c WHERE c.tag_id = t.id) AS bind_count
            FROM user_tags t
            ORDER BY t.use_count DESC, t.last_used_at DESC, t.name
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        return [_row_tag(r, bind_count=int(r["bind_count"] or 0)) for r in rows]
    finally:
        conn.close()


def management_home(*, frequent_limit: int = 12) -> dict[str, Any]:
    """探索「其他 tag」首屏：常用 + 根目录树。"""
    return {
        "frequent": list_frequent(limit=frequent_limit),
        "tree": list_tree(folder_id=None),
    }


def create_tag(
    name: str,
    *,
    folder_id: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    n = (name or "").strip()
    if not n:
        raise ValueError("tag 名称不能为空")
    if len(n) > 64:
        raise ValueError("tag 名称过长")
    tid = _new_id("tag")
    col = _norm_color(color)
    conn = get_db()
    try:
        if folder_id is not None and not _folder_exists(conn, folder_id):
            raise ValueError(f"文件夹不存在: {folder_id}")
        max_ord = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM user_tags WHERE folder_id IS ?",
            (folder_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO user_tags (id, name, color, folder_id, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tid, n, col, folder_id, int(max_ord) + 1),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM user_tags WHERE id = ?", (tid,)).fetchone()
        return _row_tag(row, bind_count=0)
    finally:
        conn.close()


def update_tag(
    tag_id: str,
    *,
    name: str | None = None,
    color: str | None = None,
    folder_id: Any = ...,
    sort_order: int | None = None,
) -> dict[str, Any]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM user_tags WHERE id = ?", (tag_id,)).fetchone()
        if not row:
            raise ValueError(f"tag 不存在: {tag_id}")
        new_name = row["name"]
        new_color = row["color"]
        new_folder = row["folder_id"]
        new_ord = int(row["sort_order"] or 0)

        if name is not None:
            n = name.strip()
            if not n:
                raise ValueError("tag 名称不能为空")
            if len(n) > 64:
                raise ValueError("tag 名称过长")
            new_name = n
        if color is not None:
            new_color = require_preset_color(color)
        if folder_id is not ...:
            fid = folder_id
            if fid is not None:
                fid = str(fid).strip() or None
            if fid is not None and not _folder_exists(conn, fid):
                raise ValueError(f"文件夹不存在: {fid}")
            new_folder = fid
        if sort_order is not None:
            new_ord = int(sort_order)

        conn.execute(
            """
            UPDATE user_tags
            SET name = ?, color = ?, folder_id = ?, sort_order = ?
            WHERE id = ?
            """,
            (new_name, new_color, new_folder, new_ord, tag_id),
        )
        # 人物 tag 改名时同步 people.name
        if name is not None:
            conn.execute(
                "UPDATE people SET name = ? WHERE tag_id = ?",
                (new_name, tag_id),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM user_tags WHERE id = ?", (tag_id,)).fetchone()
        bc = conn.execute(
            "SELECT COUNT(*) FROM chunk_user_tags WHERE tag_id = ?", (tag_id,)
        ).fetchone()[0]
        return _row_tag(row, bind_count=int(bc))
    finally:
        conn.close()


def delete_tag(tag_id: str) -> dict[str, Any]:
    try:
        from src.people import cleanup_photo_for_tag

        cleanup_photo_for_tag(tag_id)
    except Exception:
        pass
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM user_tags WHERE id = ?", (tag_id,)).fetchone()
        if not row:
            raise ValueError(f"tag 不存在: {tag_id}")
        conn.execute("DELETE FROM chunk_user_tags WHERE tag_id = ?", (tag_id,))
        conn.execute("DELETE FROM user_tags WHERE id = ?", (tag_id,))
        conn.commit()
        return {"ok": True, "id": tag_id}
    finally:
        conn.close()


def create_folder(name: str, *, parent_id: str | None = None) -> dict[str, Any]:
    n = (name or "").strip()
    if not n:
        raise ValueError("文件夹名称不能为空")
    if len(n) > 64:
        raise ValueError("文件夹名称过长")
    fid = _new_id("folder")
    conn = get_db()
    try:
        if parent_id is not None and not _folder_exists(conn, parent_id):
            raise ValueError(f"父文件夹不存在: {parent_id}")
        max_ord = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM tag_folders WHERE parent_id IS ?",
            (parent_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO tag_folders (id, name, parent_id, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (fid, n, parent_id, int(max_ord) + 1),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tag_folders WHERE id = ?", (fid,)).fetchone()
        return _row_folder(row)
    finally:
        conn.close()


def update_folder(
    folder_id: str,
    *,
    name: str | None = None,
    parent_id: Any = ...,
    sort_order: int | None = None,
) -> dict[str, Any]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM tag_folders WHERE id = ?", (folder_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"文件夹不存在: {folder_id}")
        locked = _folder_is_locked(conn, folder_id)
        new_name = row["name"]
        new_parent = row["parent_id"]
        new_ord = int(row["sort_order"] or 0)

        if name is not None:
            n = name.strip()
            if not n:
                raise ValueError("文件夹名称不能为空")
            new_name = n
        if parent_id is not ...:
            if locked:
                raise ValueError("系统文件夹「人物」不可移动")
            pid = parent_id
            if pid is not None:
                pid = str(pid).strip() or None
            if pid is not None and not _folder_exists(conn, pid):
                raise ValueError(f"父文件夹不存在: {pid}")
            if _would_cycle(conn, folder_id, pid):
                raise ValueError("不能将文件夹移动到自身或其子目录下")
            new_parent = pid
        if sort_order is not None:
            new_ord = int(sort_order)

        conn.execute(
            """
            UPDATE tag_folders
            SET name = ?, parent_id = ?, sort_order = ?
            WHERE id = ?
            """,
            (new_name, new_parent, new_ord, folder_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM tag_folders WHERE id = ?", (folder_id,)
        ).fetchone()
        return _row_folder(row)
    finally:
        conn.close()


def delete_folder(folder_id: str, *, move_up: bool = True) -> dict[str, Any]:
    """删除文件夹。move_up=True 时子文件夹与 tag 升到父级。"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM tag_folders WHERE id = ?", (folder_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"文件夹不存在: {folder_id}")
        if _folder_is_locked(conn, folder_id):
            raise ValueError("系统文件夹「人物」不可删除")
        parent = row["parent_id"]
        if move_up:
            conn.execute(
                "UPDATE tag_folders SET parent_id = ? WHERE parent_id = ?",
                (parent, folder_id),
            )
            conn.execute(
                "UPDATE user_tags SET folder_id = ? WHERE folder_id = ?",
                (parent, folder_id),
            )
        else:
            n_sub = conn.execute(
                "SELECT COUNT(*) FROM tag_folders WHERE parent_id = ?", (folder_id,)
            ).fetchone()[0]
            n_tag = conn.execute(
                "SELECT COUNT(*) FROM user_tags WHERE folder_id = ?", (folder_id,)
            ).fetchone()[0]
            if n_sub or n_tag:
                raise ValueError("文件夹非空，请先移出内容或使用 move_up")
        conn.execute("DELETE FROM tag_folders WHERE id = ?", (folder_id,))
        conn.commit()
        return {"ok": True, "id": folder_id}
    finally:
        conn.close()


def bind_chunks(tag_id: str, chunk_ids: list[str]) -> dict[str, Any]:
    ids = [str(c).strip() for c in (chunk_ids or []) if str(c).strip()]
    if not ids:
        raise ValueError("chunk_ids 不能为空")
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM user_tags WHERE id = ?", (tag_id,)).fetchone()
        if not row:
            raise ValueError(f"tag 不存在: {tag_id}")

        # 仅绑定存在的 chunk
        placeholders = ",".join("?" * len(ids))
        existing = {
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM chunks WHERE id IN ({placeholders})", ids
            ).fetchall()
        }
        if not existing:
            raise ValueError("没有有效的 chunk_id")

        added = 0
        for cid in existing:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO chunk_user_tags (chunk_id, tag_id)
                VALUES (?, ?)
                """,
                (cid, tag_id),
            )
            added += cur.rowcount

        conn.execute(
            """
            UPDATE user_tags
            SET last_used_at = datetime('now'),
                use_count = use_count + 1
            WHERE id = ?
            """,
            (tag_id,),
        )
        conn.commit()
        bc = conn.execute(
            "SELECT COUNT(*) FROM chunk_user_tags WHERE tag_id = ?", (tag_id,)
        ).fetchone()[0]
        tag = conn.execute("SELECT * FROM user_tags WHERE id = ?", (tag_id,)).fetchone()
        return {
            "ok": True,
            "tag": _row_tag(tag, bind_count=int(bc)),
            "bound": sorted(existing),
            "added": added,
        }
    finally:
        conn.close()


def list_chunks_for_tag(tag_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """按 tag 绑定列出相关 chunk（日期倒序）。"""
    tid = (tag_id or "").strip()
    if not tid:
        raise ValueError("tag_id 不能为空")
    lim = max(1, min(int(limit), 200))
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM user_tags WHERE id = ?", (tid,)
        ).fetchone()
        if not row:
            raise ValueError(f"tag 不存在: {tid}")
        rows = conn.execute(
            """
            SELECT c.id, c.date, c.text, c.source_file
            FROM chunk_user_tags cut
            JOIN chunks c ON c.id = cut.chunk_id
            WHERE cut.tag_id = ?
            ORDER BY c.date DESC, c.chunk_index ASC
            LIMIT ?
            """,
            (tid, lim),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            text = str(r["text"] or "")
            preview = text.replace("\n", " ").strip()
            if len(preview) > 160:
                preview = preview[:160] + "…"
            out.append(
                {
                    "chunk_id": r["id"],
                    "date": r["date"],
                    "source_file": r["source_file"] or "",
                    "preview": preview,
                }
            )
        return out
    finally:
        conn.close()
