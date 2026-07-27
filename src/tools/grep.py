"""Grep 工具：在 chunks 原文上做字面/子串匹配。"""

from __future__ import annotations

from typing import Any

from src.store import get_db


def _norm_date(s: str | None) -> str | None:
    t = (s or "").strip()
    return t or None


def _snippet(text: str, term: str, radius: int = 60) -> str:
    if not text:
        return ""
    idx = text.find(term)
    if idx < 0:
        return text[: radius * 2]
    left = max(0, idx - radius)
    right = min(len(text), idx + len(term) + radius)
    out = text[left:right]
    if left > 0:
        out = "…" + out
    if right < len(text):
        out = out + "…"
    return out


def grep_chunks(
    *,
    terms: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_k: int = 20,
    **_extra: Any,
) -> dict[str, Any]:
    """
    在 chunks.text 上子串匹配（大小写敏感，中文日记足够）。

    返回:
      hits: 原始命中
      chunks: 已按 chunk 聚合、可供 Context 使用的证据列表
    """
    cleaned = [str(t).strip() for t in (terms or []) if str(t).strip()]
    # 去重保序
    seen: set[str] = set()
    terms_u: list[str] = []
    for t in cleaned:
        if t not in seen:
            seen.add(t)
            terms_u.append(t)
    if not terms_u:
        return {
            "ok": True,
            "tool": "grep",
            "terms": [],
            "hits": [],
            "chunks": [],
            "count": 0,
        }

    start = _norm_date(date_from)
    end = _norm_date(date_to)
    if start and end and start > end:
        start, end = end, start

    conn = get_db()
    try:
        sql = "SELECT id, date, text, source_file FROM chunks WHERE 1=1"
        params: list[Any] = []
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        # 至少一个 term 命中（OR），缩小扫描
        like_parts = []
        for t in terms_u:
            like_parts.append("text LIKE ?")
            params.append(f"%{t}%")
        sql += " AND (" + " OR ".join(like_parts) + ")"
        sql += " ORDER BY date DESC, id"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    scored: list[dict[str, Any]] = []
    for r in rows:
        text = r["text"] or ""
        matched = [t for t in terms_u if t in text]
        if not matched:
            continue
        # 命中 term 数 + 首次出现位置靠前略加分
        score = float(len(matched))
        first = min((text.find(t) for t in matched), default=0)
        if first >= 0:
            score += max(0.0, 1.0 - first / max(len(text), 1))
        scored.append(
            {
                "chunk_id": r["id"],
                "id": r["id"],
                "date": r["date"] or "",
                "text": text,
                "source_file": r["source_file"] or "",
                "matched_terms": matched,
                "score": score,
                "snippet": _snippet(text, matched[0]),
                "source": "grep",
            }
        )

    scored.sort(key=lambda x: (-float(x["score"]), x.get("date") or "", x["id"]))
    top = scored[: max(1, int(top_k))]

    chunks = [
        {
            "id": h["id"],
            "unit_id": h["id"],
            "chunk_id": h["chunk_id"],
            "date": h["date"],
            "text": h["text"],
            "score": h["score"],
            "source": "grep",
            "matched_sentences": [],
            "matched_terms": h["matched_terms"],
            "evidence_text": h["text"],
        }
        for h in top
    ]
    return {
        "ok": True,
        "tool": "grep",
        "terms": terms_u,
        "hits": [
            {
                "chunk_id": h["chunk_id"],
                "date": h["date"],
                "snippet": h["snippet"],
                "matched_terms": h["matched_terms"],
                "score": h["score"],
            }
            for h in top
        ],
        "chunks": chunks,
        "count": len(chunks),
    }
