"""日期兜底：目录/文件名 → 正文正则（可覆盖目录）→ Agent → mtime。"""

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


def resolve_path_and_content(
    node: FileNode,
    date_pattern: str,
) -> tuple[list[ManifestEntry] | None, str | None, str | None]:
    """
    阶段 1–2：目录/文件名 → 正文正则（正文命中则覆盖目录）。

    返回 (entries|None, error|None, path_date_or_None)。
    - entries 非空：已由 path 或 content_regex 解决
    - entries 为 None 且 error 为 None：仍需 Agent / mtime；第三项为 path 候选（供 note）
    """
    try:
        text = read_file_text(node.abs_path)
    except OSError as exc:
        return None, str(exc), None

    path_date = parse_date_from_rel_path(
        node.path,
        fallback_year=int(node.mtime_date[:4]) if node.mtime_date else None,
    )

    # 2) 正文正则：命中则覆盖目录结果
    regex_entries = entries_from_regex(node, text, date_pattern)
    if regex_entries:
        return regex_entries, None, path_date

    # 1) 目录/文件名（正文未覆盖时生效）
    if path_date:
        entry = _whole_file_entry(node, text, path_date, "path")
        if entry:
            return [entry], None, path_date
        return None, "empty_or_unreadable", path_date

    # 正文与目录皆无 → 交后续 Agent / mtime；仍带回 text 由上层读文件
    if not text.strip():
        return None, "empty_or_unreadable", None
    return None, None, None


def apply_agent_or_mtime(
    node: FileNode,
    date_pattern: str,
    *,
    agent_date: str | None = None,
) -> tuple[list[ManifestEntry], str | None]:
    """
    阶段 3–4：Agent → mtime（仅当目录+正文都未解决时）。
    """
    try:
        text = read_file_text(node.abs_path)
    except OSError as exc:
        return [], str(exc)

    if agent_date and is_valid_date(agent_date):
        # 此时正文正则已确认无命中，不再让 agent 被 regex 覆盖
        entry = _whole_file_entry(node, text, agent_date, "agent")
        if entry:
            return [entry], None
        return [], "empty_or_unreadable"

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
    单文件完整级联（供测试/兼容）：
    1 目录 → 2 正文(可覆盖目录) → 3 Agent → 4 mtime
    """
    got, err, _path_date = resolve_path_and_content(node, date_pattern)
    if err:
        return [], err
    if got is not None:
        return got, None
    agent_date = suggested_date if suggested_source == "agent" else None
    return apply_agent_or_mtime(node, date_pattern, agent_date=agent_date)


def resolve_unresolved_file(
    node: FileNode,
    date_pattern: str,
) -> tuple[list[ManifestEntry], str | None]:
    return resolve_file_dates(node, date_pattern)
