"""日期级联：path 正则 → Agent（路径）→ 正文正则 → mtime。"""

from __future__ import annotations

from src.extract.dates import (
    entry_id,
    is_valid_date,
    parse_date_from_rel_path,
    split_text_by_date_pattern,
)
from src.extract.models import FileNode, ManifestEntry
from src.extract.readers import read_file_text


def entries_from_regex(
    node: FileNode,
    text: str,
    date_pattern: str,
) -> list[ManifestEntry]:
    segments = split_text_by_date_pattern(text, date_pattern)
    out: list[ManifestEntry] = []
    for i, seg in enumerate(segments):
        if not is_valid_date(seg.date):
            continue
        out.append(
            ManifestEntry(
                id=entry_id(seg.date, node.path, i),
                path=node.path,
                date=seg.date,
                date_source="content_regex",
                text=seg.content,
                confidence="high",
            )
        )
    return out


def _whole_file_entry(
    node: FileNode,
    text: str,
    date: str,
    date_source: str,
    *,
    confidence: str = "high",
) -> ManifestEntry | None:
    if not is_valid_date(date):
        return None
    body = text.strip()
    if not body:
        return None
    return ManifestEntry(
        id=entry_id(date, node.path, 0),
        path=node.path,
        date=date,
        date_source=date_source,  # type: ignore[arg-type]
        text=body,
        confidence=confidence,  # type: ignore[arg-type]
    )


def entry_from_mtime(node: FileNode, text: str) -> ManifestEntry | None:
    return _whole_file_entry(
        node, text, node.mtime_date, "mtime", confidence="low"
    )


def try_path_date(node: FileNode) -> str | None:
    """步骤 1：标准路径/文件名年月日正则。"""
    return parse_date_from_rel_path(
        node.path,
        fallback_year=int(node.mtime_date[:4]) if node.mtime_date else None,
    )


def entries_from_path(
    node: FileNode,
    text: str,
    path_date: str,
) -> list[ManifestEntry] | None:
    entry = _whole_file_entry(node, text, path_date, "path")
    return [entry] if entry else None


def entries_from_agent_date(
    node: FileNode,
    text: str,
    agent_date: str,
) -> list[ManifestEntry] | None:
    entry = _whole_file_entry(node, text, agent_date, "agent")
    return [entry] if entry else None


def resolve_after_path_failed(
    node: FileNode,
    date_pattern: str,
    *,
    agent_date: str | None = None,
) -> tuple[list[ManifestEntry], str | None]:
    """
    路径正则未命中后：Agent 日期（若有）→ 正文正则 → mtime。
    返回 (entries, error|None)。
    """
    try:
        text = read_file_text(node.abs_path)
    except OSError as exc:
        return [], str(exc)

    if not text.strip():
        return [], "empty_or_unreadable"

    # 2) Agent（路径推断出的 YYYY-MM-DD）
    if agent_date and is_valid_date(agent_date):
        got = entries_from_agent_date(node, text, agent_date)
        if got:
            return got, None
        return [], "empty_or_unreadable"

    # 3) 正文正则
    regex_entries = entries_from_regex(node, text, date_pattern)
    if regex_entries:
        return regex_entries, None

    # 4) mtime
    mtime_entry = entry_from_mtime(node, text)
    if mtime_entry:
        return [mtime_entry], None
    return [], "empty_or_unreadable"


def resolve_file_dates(
    node: FileNode,
    date_pattern: str,
    *,
    suggested_date: str | None = None,
    suggested_source: str = "agent",
) -> tuple[list[ManifestEntry], str | None]:
    """
    单文件完整级联（测试/兼容）：
    1 path → 2 agent → 3 content_regex → 4 mtime
    """
    try:
        text = read_file_text(node.abs_path)
    except OSError as exc:
        return [], str(exc)

    path_date = try_path_date(node)
    if path_date:
        got = entries_from_path(node, text, path_date)
        if got:
            return got, None
        return [], "empty_or_unreadable"

    agent_date = suggested_date if suggested_source == "agent" else None
    return resolve_after_path_failed(
        node, date_pattern, agent_date=agent_date
    )


def resolve_unresolved_file(
    node: FileNode,
    date_pattern: str,
) -> tuple[list[ManifestEntry], str | None]:
    return resolve_file_dates(node, date_pattern)
