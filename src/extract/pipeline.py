"""Extract 流水线：目录 → 正文正则(可覆盖) → Agent → mtime → Manifest。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.extract.fallback import apply_agent_or_mtime, resolve_path_and_content
from src.extract.manifest import build_created_at, default_manifest_path, save_manifest
from src.extract.models import FileRecord, Manifest, ManifestEntry
from src.extract.scan import scan_diary_tree
from src.store import load_config, resolve_diary_dir, resolve_path


def _extract_cfg() -> dict[str, Any]:
    return load_config().get("extract") or {}


def _extensions() -> set[str]:
    raw = _extract_cfg().get("extensions") or [".md", ".txt", ".docx"]
    return {str(e).lower() if str(e).startswith(".") else f".{str(e).lower()}" for e in raw}


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
    日期级联（严格顺序）：

    1. 目录/文件名（path）
    2. 正文正则（content_regex）—— 命中则覆盖目录结果
    3. Extract Agent（仅对 1+2 仍未解决的文件；需 --agent）
    4. mtime
    """
    cfg = load_config()
    date_pattern = str(
        (cfg.get("diary") or {}).get("date_pattern") or r"^# (\d{4}-\d{2}-\d{2})"
    )
    root_path = resolve_extract_root(root)
    nodes = scan_diary_tree(root_path, extensions=_extensions())

    entries: list[ManifestEntry] = []
    files: list[FileRecord] = []
    errors: list[dict[str, str]] = []
    need_later: list = []  # FileNode still unresolved after path+content
    path_hints: dict[str, str] = {}  # path → path_date（被正文覆盖时记 note）

    # —— 阶段 1–2：目录 → 正文（正文可覆盖）——
    for node in nodes:
        got, err, path_date = resolve_path_and_content(node, date_pattern)
        if path_date:
            path_hints[node.path] = path_date
        if err:
            files.append(
                FileRecord(
                    path=node.path,
                    mtime=node.mtime_iso,
                    status="error",
                    note=err,
                )
            )
            errors.append({"path": node.path, "error": err})
            continue
        if got is not None:
            entries.extend(got)
            note_parts = []
            if path_date:
                note_parts.append(f"path={path_date}")
            if got[0].date_source == "content_regex" and path_date:
                note_parts.append("overridden_by=content_regex")
            files.append(
                FileRecord(
                    path=node.path,
                    mtime=node.mtime_iso,
                    status="resolved",
                    note="; ".join(note_parts),
                )
            )
            continue
        need_later.append(node)

    # —— 阶段 3：Agent（仅 need_later）——
    agent_resolved: dict[str, str] = {}
    agent_unresolved: list[str] = [n.path for n in need_later]
    if use_agent and need_later:
        from src.extract.agent import ExtractAgent

        agent_resolved, agent_unresolved = ExtractAgent().resolve_dates(need_later)

    # —— 阶段 3–4：Agent 结果或 mtime ——
    still = {n.path: n for n in need_later}
    for path in list(still.keys()):
        node = still[path]
        agent_date = agent_resolved.get(path) if use_agent else None
        got, err = apply_agent_or_mtime(
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
        entries.extend(got)
        note_parts = []
        if path in path_hints:
            note_parts.append(f"path={path_hints[path]}")
        if agent_date:
            note_parts.append(f"agent={agent_date}")
        files.append(
            FileRecord(
                path=path,
                mtime=node.mtime_iso,
                status="resolved",
                note="; ".join(note_parts),
            )
        )

    files.sort(key=lambda f: f.path)
    entries.sort(key=lambda e: (e.date, e.path, e.id))

    root_abs = str(root_path.resolve()).replace("\\", "/")
    manifest = Manifest(
        version=1,
        root=root_abs,
        created_at=build_created_at(),
        date_pattern=date_pattern,
        files=files,
        entries=entries,
        agent_unresolved=list(agent_unresolved) if use_agent else [],
        errors=errors,
    )

    out = Path(manifest_path) if manifest_path else default_manifest_path()
    if not out.is_absolute():
        out = resolve_path(str(out))
    save_manifest(manifest, out)
    return manifest
