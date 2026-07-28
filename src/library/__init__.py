"""从前端触发：指定根目录 → extract → ingest →（可选）sentences/index。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.store import get_db, resolve_path

_lock = threading.Lock()
_STATUS: dict[str, Any] = {
    "busy": False,
    "phase": "",
    "error": None,
}


def _status_path() -> Path:
    return resolve_path("data/last_import.json")


def get_import_status() -> dict[str, Any]:
    out = dict(_STATUS)
    p = _status_path()
    if p.is_file():
        try:
            out["last"] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out["last"] = None
    else:
        out["last"] = None
    # 库规模快照
    try:
        conn = get_db()
        try:
            chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            try:
                sents = conn.execute(
                    "SELECT COUNT(*) AS n FROM rag_sentences"
                ).fetchone()["n"]
            except Exception:
                sents = 0
        finally:
            conn.close()
        out["db"] = {"chunks": int(chunks), "sentences": int(sents)}
    except Exception:
        out["db"] = {"chunks": 0, "sentences": 0}
    return out


def _save_last(payload: dict[str, Any]) -> None:
    out = _status_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_library_import(
    root: str,
    *,
    use_agent: bool = False,
    build_vectors: bool = True,
) -> dict[str, Any]:
    """
    对本机 root 目录执行建库。
    build_vectors=True 时继续 paraphrase + Chroma 索引（可问答）。
    """
    raw = (root or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("请提供日记根目录路径")

    root_path = Path(raw)
    if not root_path.is_absolute():
        root_path = resolve_path(str(root_path))
    root_path = root_path.resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"目录不存在: {root_path}")

    if not _lock.acquire(blocking=False):
        raise RuntimeError("已有导入任务进行中，请稍后再试")

    _STATUS["busy"] = True
    _STATUS["error"] = None
    started = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "ok": False,
        "root": str(root_path).replace("\\", "/"),
        "use_agent": use_agent,
        "build_vectors": build_vectors,
        "started_at": started,
        "phases": {},
    }

    try:
        from src.extract.pipeline import run_extract_pipeline
        from src.ingest import ingest_from_manifest

        _STATUS["phase"] = "extract"
        manifest = run_extract_pipeline(root=root_path, use_agent=use_agent)
        stats = manifest.stats or {}
        result["phases"]["extract"] = {
            "files_total": stats.get("files_total"),
            "entries_total": stats.get("entries_total"),
            "by_source": stats.get("by_source") or {},
            "errors": len(manifest.errors or []),
            "agent_unresolved": len(manifest.agent_unresolved or []),
        }

        _STATUS["phase"] = "ingest"
        n_chunks = ingest_from_manifest()
        result["phases"]["ingest"] = {"chunks": n_chunks}

        if build_vectors:
            from src.paraphrase.pipeline import run_paraphrase_pipeline

            _STATUS["phase"] = "sentences"
            para = run_paraphrase_pipeline(force=False)
            result["phases"]["sentences"] = para

        result["ok"] = True
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        _STATUS["phase"] = "done"
        _save_last(result)
        return result
    except Exception as exc:
        _STATUS["error"] = str(exc)
        _STATUS["phase"] = "error"
        result["ok"] = False
        result["error"] = str(exc)
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        _save_last(result)
        raise
    finally:
        _STATUS["busy"] = False
        if _STATUS["phase"] not in ("done", "error"):
            _STATUS["phase"] = ""
        _lock.release()
