"""聊天空状态推荐问题：默认召回时间段内随机抽 chunk，轻量 Agent 各生成一问。"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from src.llm import get_llm_client, get_llm_model
from src.store import get_db, load_config

QUESTION_PROMPT = """你是日记回忆助手。根据若干日记片段，为每一段各写一个自然的中文提问，供用户在聊天首页点击后去回忆。

要求：
1. 严格按片段顺序，每个片段恰好一个问题，共 {n} 个。
2. 问题像用户会问自己日记库的口语化问题（谁、做了什么、哪天、什么心情、发生了什么等）。
3. 可点出片段中的人名、地点、事件线索，但不要复述整段原文，也不要剧透细节。
4. 每个问题控制在 8～28 个汉字左右，不要编号、不要解释。
5. 严格返回 JSON，不要 markdown 代码块：
{{"questions":["问题1","问题2",...]}}
"""


def default_recall_days() -> int:
    cfg = load_config()
    raw = cfg.get("default_recall_days")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 30
    return max(1, n)


def _sq_cfg() -> dict[str, Any]:
    return dict(load_config().get("suggested_questions") or {})


def _count() -> int:
    try:
        n = int(_sq_cfg().get("count") or 5)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, 20))


def _llm_role() -> str:
    return str(_sq_cfg().get("llm_role") or "tags")


def _max_chunk_chars() -> int:
    try:
        n = int(_sq_cfg().get("max_chunk_chars") or 400)
    except (TypeError, ValueError):
        n = 400
    return max(80, n)


def recall_date_window(days: int | None = None) -> tuple[str, str]:
    """返回 (date_from, date_to)，闭区间，均为 YYYY-MM-DD。

    含今天共 N 天：即 [today-(N-1), today]。
    """
    d = default_recall_days() if days is None else max(1, int(days))
    today = date.today()
    start = today - timedelta(days=d - 1)
    return start.isoformat(), today.isoformat()


def sample_chunks_in_recall_window(
    *,
    count: int | None = None,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """在默认召回时间段内随机抽取若干 chunk。"""
    n = _count() if count is None else max(1, int(count))
    date_from, date_to = recall_date_window(days)
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, date, text, chunk_index, source_file
            FROM chunks
            WHERE date IS NOT NULL AND TRIM(date) != ''
              AND date >= ? AND date <= ?
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (date_from, date_to, n),
        ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for r in rows:
        text = str(r["text"] or "").strip()
        if not text:
            continue
        out.append(
            {
                "id": str(r["id"]),
                "date": str(r["date"]),
                "text": text,
                "chunk_index": int(r["chunk_index"] or 0),
                "source_file": str(r["source_file"] or ""),
            }
        )
    return out


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _fallback_question(chunk: dict[str, Any]) -> str:
    day = str(chunk.get("date") or "").strip()
    text = re.sub(r"\s+", " ", str(chunk.get("text") or "")).strip()
    hint = text[:18] + ("…" if len(text) > 18 else "")
    if day and hint:
        return f"{day} 那天写到「{hint}」，当时发生了什么？"
    if day:
        return f"{day} 那天的日记里记了什么？"
    if hint:
        return f"日记里提到「{hint}」是怎么回事？"
    return "最近日记里有什么值得回忆的事？"


def _parse_questions(raw: str, n: int) -> list[str]:
    data = json.loads(_strip_json_fence(raw))
    items = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("questions 字段缺失或不是列表")
    out: list[str] = []
    for q in items:
        s = re.sub(r"^\s*\d+[\.\)、]\s*", "", str(q or "").strip())
        s = s.strip("「」\"'")
        if s:
            out.append(s)
        if len(out) >= n:
            break
    return out


def _generate_questions(chunks: list[dict[str, Any]]) -> list[str]:
    """对每个 chunk 分别生成一问（一次轻量调用批量完成）。"""
    if not chunks:
        return []

    n = len(chunks)
    max_chars = _max_chunk_chars()
    parts: list[str] = []
    for i, c in enumerate(chunks, start=1):
        text = re.sub(r"\s+", " ", str(c.get("text") or "")).strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        parts.append(f"[{i}] 日期：{c.get('date') or '未知'}\n{text}")

    user = "日记片段：\n\n" + "\n\n".join(parts)
    client = get_llm_client(_llm_role())
    resp = client.chat.completions.create(
        model=get_llm_model(_llm_role()),
        messages=[
            {"role": "system", "content": QUESTION_PROMPT.format(n=n)},
            {"role": "user", "content": user},
        ],
        temperature=0.7,
    )
    raw = (resp.choices[0].message.content or "").strip()
    return _parse_questions(raw, n)


def generate_suggested_questions(
    *,
    count: int | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """随机抽 chunk → 轻量 Agent 生成推荐问题。"""
    n = _count() if count is None else max(1, int(count))
    date_from, date_to = recall_date_window(days)
    chunks = sample_chunks_in_recall_window(count=n, days=days)

    if not chunks:
        return {
            "questions": [],
            "count": 0,
            "date_from": date_from,
            "date_to": date_to,
            "recall_days": days if days is not None else default_recall_days(),
            "chunk_ids": [],
            "source": "empty",
        }

    questions: list[str] = []
    source = "llm"
    try:
        questions = _generate_questions(chunks)
    except Exception:
        source = "fallback"
        questions = []

    # 不足则用兜底问题补齐；过长则截断
    while len(questions) < len(chunks):
        questions.append(_fallback_question(chunks[len(questions)]))
        source = "fallback" if source != "llm" else "llm+fallback"
    questions = questions[: len(chunks)]

    return {
        "questions": questions,
        "count": len(questions),
        "date_from": date_from,
        "date_to": date_to,
        "recall_days": days if days is not None else default_recall_days(),
        "chunk_ids": [c["id"] for c in chunks],
        "source": source,
    }
