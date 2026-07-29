"""写日记落盘 + 跨日归档入库。

工作流：
1. 前端持续把文稿同步到 data/write_diary/manuscripts.json
2. 日期切换（本地日历日）时，把上一天的文稿写成 archived/YYYY-MM-DD.md
3. 将该文件切块写入 SQLite chunks，日历即可着色/阅读
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.ingest import DiaryEntry, entry_to_chunks, file_hash
from src.store import delete_chunks_by_source, get_db, load_config, resolve_path, save_chunks

_lock = threading.Lock()
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _cfg() -> dict[str, Any]:
    return load_config().get("write_diary") or {}


def write_root() -> Path:
    rel = str(_cfg().get("root") or "data/write_diary")
    path = resolve_path(rel)
    path.mkdir(parents=True, exist_ok=True)
    (path / "archived").mkdir(parents=True, exist_ok=True)
    return path


def manuscripts_path() -> Path:
    return write_root() / "manuscripts.json"


def archived_path(day: str) -> Path:
    return write_root() / "archived" / f"{day}.md"


def today_str() -> str:
    """本机本地日历日（跨日判断用）。"""
    return date.today().isoformat()


def _blank_item() -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id": f"ms_{int(datetime.now().timestamp() * 1000)}",
        "title": "",
        "content": "",
        "createdAt": now,
        "updatedAt": now,
    }


def _default_state() -> dict[str, Any]:
    return {
        "mode": "papers",
        "active_day": today_str(),
        "items": [_blank_item()],
        "ingested_days": [],
    }


def load_state() -> dict[str, Any]:
    path = manuscripts_path()
    if not path.is_file():
        state = _default_state()
        save_state(state)
        return state
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = _default_state()
        save_state(state)
        return state

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        items = [_blank_item()]
    mode = data.get("mode") if isinstance(data, dict) else "papers"
    if mode not in {"papers", "chat"}:
        mode = "papers"
    active = str((data or {}).get("active_day") or today_str())
    if not _DATE_RE.fullmatch(active):
        active = today_str()
    ingested = (data or {}).get("ingested_days") or []
    if not isinstance(ingested, list):
        ingested = []
    return {
        "mode": mode,
        "active_day": active,
        "items": items,
        "ingested_days": [d for d in ingested if isinstance(d, str) and _DATE_RE.fullmatch(d)],
    }


def save_state(state: dict[str, Any]) -> None:
    path = manuscripts_path()
    payload = {
        "mode": state.get("mode") or "papers",
        "active_day": state.get("active_day") or today_str(),
        "items": state.get("items") or [_blank_item()],
        "ingested_days": state.get("ingested_days") or [],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _item_has_content(item: dict[str, Any]) -> bool:
    title = str(item.get("title") or "").strip()
    content = str(item.get("content") or "").strip()
    return bool(title or content)


def manuscripts_to_markdown(day: str, items: list[dict[str, Any]]) -> str:
    parts: list[str] = [f"# {day}", ""]
    usable = [it for it in items if _item_has_content(it)]
    if not usable:
        return ""
    for i, item in enumerate(usable, start=1):
        title = str(item.get("title") or "").strip() or f"新建文稿{i}"
        content = str(item.get("content") or "").rstrip()
        parts.append(f"## {title}")
        parts.append("")
        if content:
            parts.append(content)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def archive_day(day: str, items: list[dict[str, Any]]) -> Path | None:
    """把某日文稿写成 archived/YYYY-MM-DD.md；无内容则返回 None。"""
    if not _DATE_RE.fullmatch(day):
        raise ValueError(f"日期格式须为 YYYY-MM-DD，收到: {day}")
    md = manuscripts_to_markdown(day, items)
    if not md.strip():
        return None
    path = archived_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return path


def ingest_archived_day(day: str) -> dict[str, Any]:
    """将 archived/{day}.md 切块入库（覆盖同 source 旧块）。"""
    path = archived_path(day)
    if not path.is_file():
        return {"date": day, "chunks": 0, "skipped": True, "reason": "no_file"}

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {"date": day, "chunks": 0, "skipped": True, "reason": "empty"}

    # 去掉首行 # YYYY-MM-DD，避免重复标题进 chunk
    lines = text.splitlines()
    if lines and lines[0].strip() == f"# {day}":
        body = "\n".join(lines[1:]).strip()
    else:
        body = text
    if not body:
        return {"date": day, "chunks": 0, "skipped": True, "reason": "empty_body"}

    source = f"write_diary/{day}.md"
    cfg = load_config()
    max_chars = int((cfg.get("chunking") or {}).get("max_chars") or 500)
    overlap = int((cfg.get("chunking") or {}).get("overlap_chars") or 50)

    entry = DiaryEntry(date=day, content=body, source_file=source)
    chunks = entry_to_chunks(entry, max_chars, overlap)

    conn = get_db()
    try:
        delete_chunks_by_source(source, conn)
        delete_chunks_by_source(path.name, conn)
        save_chunks(chunks, conn)
        conn.execute(
            """INSERT OR REPLACE INTO ingest_log
               (source_file, file_hash, chunk_count)
               VALUES (?, ?, ?)""",
            (source, file_hash(path), len(chunks)),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "date": day,
        "chunks": len(chunks),
        "source_file": source,
        "path": str(path).replace("\\", "/"),
        "skipped": False,
    }


def _rollover_unlocked(state: dict[str, Any]) -> dict[str, Any]:
    """若 active_day < 今天，归档并入库，重置为今日空白文稿。"""
    today = today_str()
    active = str(state.get("active_day") or today)
    result: dict[str, Any] = {
        "today": today,
        "active_day": active,
        "rolled": False,
        "archived": [],
        "ingested": [],
    }

    if active >= today:
        # 仍写「今日草稿」快照，便于崩溃恢复
        draft = write_root() / "draft_today.md"
        md = manuscripts_to_markdown(today, state.get("items") or [])
        if md.strip():
            draft.write_text(md, encoding="utf-8")
        elif draft.is_file():
            draft.unlink()
        result["active_day"] = active
        return result

    auto_ingest = bool(_cfg().get("auto_ingest", True))
    archived_days: list[str] = []
    ingested: list[dict[str, Any]] = []

    # 把当前文稿记到 active_day（通常是昨天；若隔了多天也只归档这一份）
    path = archive_day(active, state.get("items") or [])
    if path is not None:
        archived_days.append(active)
        if auto_ingest:
            info = ingest_archived_day(active)
            ingested.append(info)
            if not info.get("skipped"):
                days = list(state.get("ingested_days") or [])
                if active not in days:
                    days.append(active)
                state["ingested_days"] = days

    # 若隔了多天，中间空日不造假日记；只推进到今天
    state["active_day"] = today
    state["items"] = [_blank_item()]
    state["mode"] = state.get("mode") or "papers"
    save_state(state)

    draft = write_root() / "draft_today.md"
    if draft.is_file():
        draft.unlink()

    result.update(
        {
            "rolled": True,
            "active_day": today,
            "archived": archived_days,
            "ingested": ingested,
            "items_reset": True,
        }
    )
    return result


def ensure_day_rollover() -> dict[str, Any]:
    with _lock:
        state = load_state()
        info = _rollover_unlocked(state)
        state = load_state()
        return {
            **info,
            "mode": state.get("mode"),
            "items": state.get("items"),
            "active_day": state.get("active_day"),
            "ingested_days": state.get("ingested_days") or [],
        }


def get_manuscripts() -> dict[str, Any]:
    """读取文稿；必要时先做跨日归档。"""
    with _lock:
        state = load_state()
        rollover = _rollover_unlocked(state)
        state = load_state()
        return {
            "mode": state.get("mode") or "papers",
            "active_day": state.get("active_day") or today_str(),
            "items": state.get("items") or [_blank_item()],
            "ingested_days": state.get("ingested_days") or [],
            "rollover": {
                "rolled": rollover.get("rolled"),
                "archived": rollover.get("archived"),
                "ingested": rollover.get("ingested"),
            },
            "today": today_str(),
        }


def sync_manuscripts(
    *,
    mode: str | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """前端保存文稿；若已跨日则先归档再写入今日内容。"""
    with _lock:
        state = load_state()
        rollover = _rollover_unlocked(state)
        state = load_state()

        if mode in {"papers", "chat"}:
            state["mode"] = mode
        if isinstance(items, list) and items:
            cleaned: list[dict[str, Any]] = []
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                cleaned.append(
                    {
                        "id": str(raw.get("id") or _blank_item()["id"]),
                        "title": str(raw.get("title") or ""),
                        "content": str(raw.get("content") or ""),
                        "createdAt": str(raw.get("createdAt") or datetime.now().isoformat(timespec="seconds")),
                        "updatedAt": str(raw.get("updatedAt") or datetime.now().isoformat(timespec="seconds")),
                    }
                )
            if cleaned:
                state["items"] = cleaned
        save_state(state)

        # 同步今日草稿 md
        draft = write_root() / "draft_today.md"
        md = manuscripts_to_markdown(state["active_day"], state["items"])
        if md.strip():
            draft.write_text(md, encoding="utf-8")
        elif draft.is_file():
            draft.unlink()

        return {
            "ok": True,
            "mode": state["mode"],
            "active_day": state["active_day"],
            "items": state["items"],
            "ingested_days": state.get("ingested_days") or [],
            "rollover": {
                "rolled": rollover.get("rolled"),
                "archived": rollover.get("archived"),
                "ingested": rollover.get("ingested"),
            },
            "today": today_str(),
            "root": str(write_root()).replace("\\", "/"),
        }


def force_archive_active_day() -> dict[str, Any]:
    """手动把当前文稿按 active_day 归档入库（不推进日期，不重置文稿）。调试用。"""
    with _lock:
        state = load_state()
        day = str(state.get("active_day") or today_str())
        path = archive_day(day, state.get("items") or [])
        if path is None:
            return {"ok": False, "date": day, "reason": "empty"}
        info = ingest_archived_day(day)
        days = list(state.get("ingested_days") or [])
        if day not in days and not info.get("skipped"):
            days.append(day)
            state["ingested_days"] = days
            save_state(state)
        return {"ok": True, "date": day, "path": str(path).replace("\\", "/"), **info}
