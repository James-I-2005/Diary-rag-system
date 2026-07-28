"""召回日期过滤：支持日期集合，或兼容旧的 from~to 闭区间。"""

from __future__ import annotations

import re
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_date_list(dates: list[str] | None) -> list[str]:
    """去重、校验、排序；非法项丢弃。"""
    out: list[str] = []
    seen: set[str] = set()
    for d in dates or []:
        s = str(d or "").strip()
        if not _DATE_RE.fullmatch(s) or s in seen:
            continue
        seen.add(s)
        out.append(s)
    out.sort()
    return out


def dates_from_structured(structured: Any) -> list[str] | None:
    """
    优先读 dates 集合；非空则返回排序列表。
    无集合时返回 None（再看区间）。
    """
    if structured is None:
        return None
    raw = getattr(structured, "dates", None)
    if raw is None and isinstance(structured, dict):
        raw = structured.get("dates")
    norm = normalize_date_list(list(raw) if raw else [])
    return norm or None


def date_bounds_from_structured(structured: Any) -> tuple[str | None, str | None]:
    """从 StructuredQuery / 任意对象读取归一化日期闭区间（无集合时使用）。"""
    if structured is None:
        return None, None
    # 若已有 dates 集合，区间取 min/max（供展示）；过滤应以集合为准
    dset = dates_from_structured(structured)
    if dset:
        return dset[0], dset[-1]
    if hasattr(structured, "date_range") and callable(structured.date_range):
        return structured.date_range()
    start = (getattr(structured, "date_from", None) or "").strip() or None
    end = (getattr(structured, "date_to", None) or "").strip() or None
    if start and end and start > end:
        start, end = end, start
    return start, end


def date_allowed(date: str, structured: Any) -> bool:
    """某日是否落在结构化查询的过滤内；无过滤则恒 True。"""
    d = (date or "").strip()
    dset = dates_from_structured(structured)
    if dset is not None:
        return d in set(dset)
    start, end = date_bounds_from_structured(structured)
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True
