"""Extract 流水线：扫盘 → path 正则 → Agent(路径) → 正文正则 → mtime → Manifest。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.extract.fallback import (
    entries_from_path,
    resolve_after_path_failed,
    try_path_date,
)
from src.extract.manifest import build_created_at, default_manifest_path, save_manifest
from src.extract.models import FileNode, FileRecord, Manifest, ManifestEntry
from src.extract.readers import read_file_text
from src.extract.scan import scan_diary_tree
from src.store import load_config, resolve_diary_dir, resolve_path


def _extract_cfg() -> dict[str, Any]:
    return load_config().get("extract") or {}


def _extensions() -> set[str]:
    raw = _extract_cfg().get("extensions") or [".md", ".txt", ".docx"]
    return {
        str(e).lower() if str(e).startswith(".") else f".{str(e).lower()}"
        for e in raw
    }


def resolve_extract_root(root: str | Path | None = None) -> Path:
    if root is None or str(root).strip() == "":
        return resolve_diary_dir()
    p = Path(str(root))
    if not p.is_absolute():
        p = resolve_path(str(p))
    return p.resolve()


def run_extract_pipeline(
    root: str | Path | None = None,
    *,
    use_agent: bool = False,
    manifest_path: str | Path | None = None,
) -> Manifest:
    """
    维护「已正确提取」的 Manifest 条目集合；对根目录递归每个文件：

    1. 路径标准年月日正则（path）
    2. 未解决 → 轻量 Agent 只看路径 → date | unknown（需 --agent）
    3. 仍未解决 → 正文正则（content_regex）
    4. 再不行 → mtime
    """
    cfg = load_config()
    date_pattern = str(
        (cfg.get("diary") or {}).get("date_pattern") or r"^# (\d{4}-\d{2}-\d{2})"
    )
    root_path = resolve_extract_root(root)
    nodes = scan_diary_tree(root_path, extensions=_extensions())

    # 已正确提取的集合（随遍历增长）
    resolved_entries: list[ManifestEntry] = []
    files: list[FileRecord] = []
    errors: list[dict[str, str]] = []

    path_ok: list[FileNode] = []
    need_agent: list[FileNode] = []

    # —— 步骤 1：路径正则 ——
    for node in nodes:
        try:
            text = read_file_text(node.abs_path)
        except OSError as exc:
            files.append(
                FileRecord(
                    path=node.path,
                    mtime=node.mtime_iso,
                    status="error",
                    note=str(exc),
                )
            )
            errors.append({"path": node.path, "error": str(exc)})
            continue

        if not text.strip():
            files.append(
                FileRecord(
                    path=node.path,
                    mtime=node.mtime_iso,
                    status="error",
                    note="empty_or_unreadable",
                )
            )
            errors.append({"path": node.path, "error": "empty_or_unreadable"})
            continue

        path_date = try_path_date(node)
        if path_date:
            got = entries_from_path(node, text, path_date)
            if got:
                resolved_entries.extend(got)
                path_ok.append(node)
                files.append(
                    FileRecord(
                        path=node.path,
                        mtime=node.mtime_iso,
                        status="resolved",
                        note=f"path={path_date}",
                    )
                )
                continue
            files.append(
                FileRecord(
                    path=node.path,
                    mtime=node.mtime_iso,
                    status="error",
                    note="empty_or_unreadable",
                )
            )
            errors.append({"path": node.path, "error": "empty_or_unreadable"})
            continue

        need_agent.append(node)

    # —— 步骤 2：轻量 Agent（仅路径未解析的文件；可选）——
    agent_resolved: dict[str, str] = {}
    agent_unknown: list[str] = [n.path for n in need_agent]
    if use_agent and need_agent:
        from src.extract.agent import ExtractAgent

        agent_resolved, agent_unknown = ExtractAgent().resolve_dates(need_agent)

    # —— 步骤 2–4：Agent → 正文 → mtime ——
    pending = {n.path: n for n in need_agent}
    for path, node in pending.items():
        agent_date = agent_resolved.get(path) if use_agent else None
        got, err = resolve_after_path_failed(
            node, date_pattern, agent_date=agent_date
        )
        if err:
            files.append(
                FileRecord(
                    path=path,
                    mtime=node.mtime_iso,
                    status="error",
                    note=err,
                )
            )
            errors.append({"path": path, "error": err})
            continue
        resolved_entries.extend(got)
        note_parts = []
        if agent_date:
            note_parts.append(f"agent={agent_date}")
        src = got[0].date_source if got else ""
        if src:
            note_parts.append(f"source={src}")
        files.append(
            FileRecord(
                path=path,
                mtime=node.mtime_iso,
                status="resolved",
                note="; ".join(note_parts),
            )
        )

    files.sort(key=lambda f: f.path)
    resolved_entries.sort(key=lambda e: (e.date, e.path, e.id))

    root_abs = str(root_path.resolve()).replace("\\", "/")
    manifest = Manifest(
        version=1,
        root=root_abs,
        created_at=build_created_at(),
        date_pattern=date_pattern,
        files=files,
        entries=resolved_entries,
        agent_unresolved=list(agent_unknown) if use_agent else [],
        errors=errors,
    )

    out = Path(manifest_path) if manifest_path else default_manifest_path()
    if not out.is_absolute():
        out = resolve_path(str(out))
    save_manifest(manifest, out)
    return manifest
