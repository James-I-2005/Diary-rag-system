"""Manifest 读写与统计。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.extract.models import Manifest, ManifestEntry
from src.store import resolve_path


def default_manifest_path() -> Path:
    cfg = {}
    try:
        from src.store import load_config

        cfg = load_config().get("extract") or {}
    except Exception:
        pass
    raw = cfg.get("manifest_path") or "data/extract_manifest.json"
    return resolve_path(str(raw))


def compute_stats(entries: list[ManifestEntry], files_total: int) -> dict[str, Any]:
    by_source = Counter(e.date_source for e in entries)
    return {
        "files_total": files_total,
        "entries_total": len(entries),
        "by_source": dict(by_source),
    }


def build_created_at() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def save_manifest(manifest: Manifest, path: Path | None = None) -> Path:
    out = path or default_manifest_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest.stats = compute_stats(manifest.entries, len(manifest.files))
    out.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def load_manifest(path: Path | str | None = None) -> Manifest:
    p = Path(path) if path else default_manifest_path()
    if not p.is_absolute():
        p = resolve_path(str(p))
    data = json.loads(p.read_text(encoding="utf-8"))
    return Manifest.from_dict(data)
