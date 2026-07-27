"""Extract 中间态模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

DateSource = Literal["path", "agent", "content_regex", "mtime"]
FileStatus = Literal["resolved", "error", "pending"]


@dataclass
class FileNode:
    """扫盘得到的文件节点（相对 root）。"""

    path: str
    abs_path: str
    mtime_iso: str
    mtime_date: str  # YYYY-MM-DD
    size: int


@dataclass
class ManifestEntry:
    """一篇有日期的日记（Manifest 原子单位）。"""

    id: str
    path: str
    date: str
    date_source: DateSource
    text: str
    confidence: Literal["high", "low"] = "high"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileRecord:
    path: str
    mtime: str
    status: FileStatus = "pending"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Manifest:
    version: int = 1
    root: str = ""
    created_at: str = ""
    date_pattern: str = ""
    files: list[FileRecord] = field(default_factory=list)
    entries: list[ManifestEntry] = field(default_factory=list)
    agent_unresolved: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "root": self.root,
            "created_at": self.created_at,
            "date_pattern": self.date_pattern,
            "files": [f.to_dict() for f in self.files],
            "entries": [e.to_dict() for e in self.entries],
            "agent_unresolved": list(self.agent_unresolved),
            "errors": list(self.errors),
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        files = [
            FileRecord(
                path=str(f.get("path") or ""),
                mtime=str(f.get("mtime") or ""),
                status=f.get("status") or "pending",  # type: ignore[arg-type]
                note=str(f.get("note") or ""),
            )
            for f in (data.get("files") or [])
        ]
        entries = [
            ManifestEntry(
                id=str(e.get("id") or ""),
                path=str(e.get("path") or ""),
                date=str(e.get("date") or ""),
                date_source=e.get("date_source") or "mtime",  # type: ignore[arg-type]
                text=str(e.get("text") or ""),
                confidence=e.get("confidence") or "high",  # type: ignore[arg-type]
            )
            for e in (data.get("entries") or [])
        ]
        return cls(
            version=int(data.get("version") or 1),
            root=str(data.get("root") or ""),
            created_at=str(data.get("created_at") or ""),
            date_pattern=str(data.get("date_pattern") or ""),
            files=files,
            entries=entries,
            agent_unresolved=[str(p) for p in (data.get("agent_unresolved") or [])],
            errors=list(data.get("errors") or []),
            stats=dict(data.get("stats") or {}),
        )
