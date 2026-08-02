"""从用户问题解析显式 @tag mention。"""

from __future__ import annotations

import re
from typing import Any

from src.store import get_db

# @Tag名：允许中英文、数字、下划线、连字符；到空白或标点结束
_MENTION_RE = re.compile(r"@([^\s@，。！？、；：,.!?;:，\n\r\t]+)")


def extract_mention_names(text: str) -> list[str]:
    """保序去重的 @后面的名字。"""
    names: list[str] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text or ""):
        name = (m.group(1) or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def resolve_tags_by_names(names: list[str]) -> list[dict[str, Any]]:
    """按 name 精确匹配 user_tags（大小写敏感）；返回 {id,name,color}。"""
    cleaned = [str(n).strip() for n in names if str(n).strip()]
    if not cleaned:
        return []
    conn = get_db()
    try:
        out: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for name in cleaned:
            row = conn.execute(
                "SELECT id, name, color FROM user_tags WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
            if not row:
                # 尝试去首尾常见后缀噪音
                continue
            tid = row["id"]
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            out.append(
                {
                    "id": tid,
                    "name": row["name"],
                    "color": row["color"] or "#6b7280",
                }
            )
        return out
    finally:
        conn.close()


def resolve_mentions(text: str) -> dict[str, Any]:
    """解析问题中的 @tag。"""
    names = extract_mention_names(text)
    tags = resolve_tags_by_names(names)
    found = {t["name"] for t in tags}
    missing = [n for n in names if n not in found]
    return {
        "mention_names": names,
        "tags": tags,
        "missing_names": missing,
    }
