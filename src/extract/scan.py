"""目录扫描。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.extract.models import FileNode

_DEFAULT_EXTS = {".md", ".txt"}


def scan_diary_tree(
    root: Path,
    *,
    extensions: set[str] | None = None,
) -> list[FileNode]:
    """递归扫描 root，返回相对路径 FileNode 列表（按 path 排序）。"""
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"extract root 不存在或不是目录: {root}")

    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or _DEFAULT_EXTS)}
    nodes: list[FileNode] = []

    for dirpath, _dirnames, filenames in os_walk_sorted(root):
        base = Path(dirpath)
        for name in sorted(filenames):
            fp = base / name
            if fp.suffix.lower() not in exts:
                continue
            if not fp.is_file():
                continue
            try:
                rel = fp.relative_to(root).as_posix()
            except ValueError:
                continue
            st = fp.stat()
            mtime = datetime.fromtimestamp(st.st_mtime)
            nodes.append(
                FileNode(
                    path=rel,
                    abs_path=str(fp),
                    mtime_iso=mtime.isoformat(timespec="seconds"),
                    mtime_date=mtime.strftime("%Y-%m-%d"),
                    size=int(st.st_size),
                )
            )

    nodes.sort(key=lambda n: n.path)
    return nodes


def os_walk_sorted(root: Path):
    """与 os.walk 相同，但目录名排序，便于可复现。"""
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        yield dirpath, dirnames, filenames


def peek_file(abs_path: str, n_chars: int = 200) -> str:
    """读取文件前 n 字，供 Agent 弱线索。"""
    try:
        text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[: max(0, n_chars)]
