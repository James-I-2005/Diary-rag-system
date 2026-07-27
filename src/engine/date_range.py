"""召回日期范围工具。"""

from __future__ import annotations

from typing import Any


def date_bounds_from_structured(structured: Any) -> tuple[str | None, str | None]:
    """从 StructuredQuery / 任意对象读取归一化日期闭区间。"""
    if structured is None:
        return None, None
    if hasattr(structured, "date_range") and callable(structured.date_range):
        return structured.date_range()
    start = (getattr(structured, "date_from", None) or "").strip() or None
    end = (getattr(structured, "date_to", None) or "").strip() or None
    if start and end and start > end:
        start, end = end, start
    return start, end
