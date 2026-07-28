"""按日期查询已入库日记（chunks）。"""

from __future__ import annotations

import re
from typing import Any

from src.store import get_db, load_config


def list_diary_dates() -> dict[str, Any]:
    """返回有 chunk 的所有日期及条数。"""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT date, COUNT(*) AS n
            FROM chunks
            WHERE date IS NOT NULL AND TRIM(date) != ''
            GROUP BY date
            ORDER BY date
            """
        ).fetchall()
    finally:
        conn.close()

    dates = [str(r["date"]) for r in rows]
    counts = {str(r["date"]): int(r["n"]) for r in rows}
    return {
        "dates": dates,
        "counts": counts,
        "min_date": dates[0] if dates else None,
        "max_date": dates[-1] if dates else None,
        "total_days": len(dates),
    }


def _max_overlap_chars() -> int:
    """去重搜索上限：略大于切块 overlap，兜住 strip 后仍重叠的片段。"""
    try:
        ov = int((load_config().get("chunking") or {}).get("overlap_chars", 50))
    except (TypeError, ValueError):
        ov = 50
    return max(80, ov * 3)


def _longest_suffix_prefix_overlap(prev: str, nxt: str, max_len: int) -> int:
    """
    找 prev 后缀与 nxt 前缀的最长相等长度（用于去掉切块重叠）。
    至少 8 字才认重叠，避免误伤短词。
    """
    if not prev or not nxt:
        return 0
    limit = min(len(prev), len(nxt), max_len)
    min_hit = 8
    for length in range(limit, min_hit - 1, -1):
        if prev[-length:] == nxt[:length]:
            return length
    return 0


def _merge_two_chunks(prev: str, nxt: str, max_overlap: int) -> str:
    """去掉 nxt 开头与 prev 尾部的重复，并理顺换行衔接。"""
    a = prev.rstrip()
    b = nxt.lstrip("\n\r")
    if not a:
        return b
    if not b:
        return a

    ov = _longest_suffix_prefix_overlap(a, b, max_overlap)
    if ov > 0:
        rest = b[ov:]
        # 重叠已接上前文；去掉 rest 开头多余空白/空行
        rest = rest.lstrip(" \t")
        if rest.startswith("\r\n"):
            rest = rest[2:]
        elif rest.startswith("\n") or rest.startswith("\r"):
            rest = rest[1:]
        rest = rest.lstrip(" \t")
        if not rest:
            return a
        if rest.startswith("\n"):
            return a + rest
        # 前文已结句且后文非标点续写 → 空行分段；否则直接粘上
        if a[-1] in "。！？…」』\"'" and not rest.startswith(
            ("，", "、", "；", "：", "」", "』")
        ):
            return a + "\n\n" + rest
        return a + rest

    return a + "\n\n" + b.lstrip()


def stitch_chunk_texts(
    chunks: list[dict[str, Any]],
    *,
    max_overlap: int | None = None,
) -> str:
    """
    按 source_file + chunk_index 顺序拼合，去掉相邻块切块重叠。
    不同 source_file 之间用空行隔开，不做跨文件去重。
    """
    if not chunks:
        return ""
    limit = max_overlap if max_overlap is not None else _max_overlap_chars()

    parts: list[str] = []
    cur_source: str | None = None
    buf = ""

    def flush() -> None:
        nonlocal buf
        text = buf.strip()
        if text:
            parts.append(text)
        buf = ""

    for c in chunks:
        raw = (c.get("text") or "").strip()
        if not raw:
            continue
        src = str(c.get("source_file") or "")
        if cur_source is None:
            cur_source = src
            buf = raw
            continue
        if src != cur_source:
            flush()
            cur_source = src
            buf = raw
            continue
        buf = _merge_two_chunks(buf, raw, limit)

    flush()
    return "\n\n".join(parts)


def get_diary_by_date(date: str) -> dict[str, Any]:
    """拼合某日全部 chunk 原文（相邻块去重叠）。"""
    s = (date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise ValueError(f"日期格式须为 YYYY-MM-DD，收到: {s}")

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, date, text, chunk_index, source_file
            FROM chunks
            WHERE date = ?
            ORDER BY source_file, chunk_index, id
            """,
            (s,),
        ).fetchall()
    finally:
        conn.close()

    chunks = [
        {
            "id": r["id"],
            "source_file": r["source_file"] or "",
            "chunk_index": int(r["chunk_index"] or 0),
            "text": r["text"] or "",
        }
        for r in rows
    ]
    text = stitch_chunk_texts(chunks)
    return {
        "date": s,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "text": text,
    }
