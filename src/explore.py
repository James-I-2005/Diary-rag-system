"""探索页：全文搜索 / 人物 / 地点 / 其它标签。"""

from __future__ import annotations

import json
import re
from typing import Any

from src.store import get_db


def _snippet(text: str, needle: str, radius: int = 72) -> str:
    if not text:
        return ""
    idx = text.find(needle)
    if idx < 0:
        flat = text.replace("\n", " ").strip()
        return flat[: radius * 2] + ("…" if len(flat) > radius * 2 else "")
    left = max(0, idx - radius)
    right = min(len(text), idx + len(needle) + radius)
    out = text[left:right].replace("\n", " ")
    if left > 0:
        out = "…" + out
    if right < len(text):
        out = out + "…"
    return out


def _query_tokens(q: str) -> list[str]:
    """拆出用于相近匹配的词：空白分词 + jieba（若可用）。"""
    parts = [p for p in re.split(r"\s+", q) if p]
    tokens: list[str] = []
    seen: set[str] = set()

    def add(t: str) -> None:
        t = t.strip()
        if len(t) < 2 or t in seen:
            return
        seen.add(t)
        tokens.append(t)

    for p in parts:
        add(p)

    try:
        import jieba

        for w in jieba.lcut(q):
            add(w)
    except Exception:
        pass

    # 纯中文短查询：补 2 字窗口，便于「相近」
    compact = re.sub(r"\s+", "", q)
    if len(compact) >= 3 and not re.search(r"[A-Za-z0-9]", compact):
        for i in range(len(compact) - 1):
            add(compact[i : i + 2])
    return tokens


def _bigram_hits(text: str, q: str) -> tuple[int, int]:
    compact_q = re.sub(r"\s+", "", q)
    if len(compact_q) < 2:
        return 0, 0
    grams = [compact_q[i : i + 2] for i in range(len(compact_q) - 1)]
    if not grams:
        return 0, 0
    hit = sum(1 for g in grams if g in text)
    return hit, len(grams)


def _near_score(text: str, q: str, tokens: list[str]) -> float:
    """>0 表示可视为相近命中。"""
    if not text or not q:
        return 0.0
    if q in text:
        return 0.0  # 完全匹配另算

    score = 0.0
    if tokens:
        hit = [t for t in tokens if t in text]
        if not hit:
            return 0.0
        ratio = len(hit) / len(tokens)
        # 至少命中一半词，或命中 >=2 个实质词
        if ratio < 0.5 and len(hit) < 2:
            return 0.0
        score += ratio * 2.0 + len(hit) * 0.15
    else:
        hit, total = _bigram_hits(text, q)
        if total == 0 or hit / total < 0.5:
            return 0.0
        score += hit / total

    # 忽略大小写的连续命中也算很强的「近」
    if q.casefold() in text.casefold() and q not in text:
        score += 1.5

    return score


def _row_to_item(
    r: Any,
    *,
    match: str,
    score: float,
    needle: str,
) -> dict[str, Any]:
    text = str(r["text"] or "")
    return {
        "chunk_id": r["id"],
        "date": r["date"] or "",
        "source_file": r["source_file"] or "",
        "preview": _snippet(text, needle),
        "match": match,  # exact | near
        "score": round(score, 4),
        "matched": needle,
    }


def search_chunks(query: str, *, limit: int = 50) -> dict[str, Any]:
    """
    对 chunks 原文做 grep：
    1) exact：连续子串完全命中
    2) near：分词/字窗多数命中，但非整句连续匹配
    返回顺序：先 exact，再 near。
    """
    q = (query or "").strip()
    if not q:
        return {"query": "", "exact": [], "near": [], "items": [], "total": 0}

    limit = max(1, min(int(limit), 100))
    tokens = _query_tokens(q)

    conn = get_db()
    try:
        like_parts = ["text LIKE ?"]
        params: list[Any] = [f"%{q}%"]
        for t in tokens[:12]:
            like_parts.append("text LIKE ?")
            params.append(f"%{t}%")
        sql = f"""
            SELECT id, date, text, source_file, chunk_index
            FROM chunks
            WHERE {" OR ".join(like_parts)}
            ORDER BY date DESC, chunk_index ASC, id ASC
        """
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    exact: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    seen: set[str] = set()

    for r in rows:
        cid = str(r["id"])
        text = str(r["text"] or "")
        if not text:
            continue
        if q in text:
            if cid in seen:
                continue
            seen.add(cid)
            count = text.count(q)
            first = text.find(q)
            score = 100.0 + count + max(0.0, 1.0 - first / max(len(text), 1))
            exact.append(_row_to_item(r, match="exact", score=score, needle=q))
            continue

        ns = _near_score(text, q, tokens)
        if ns <= 0:
            continue
        if cid in seen:
            continue
        seen.add(cid)
        needle = next((t for t in tokens if t in text), q[:2] if len(q) >= 2 else q)
        near.append(_row_to_item(r, match="near", score=ns, needle=needle))

    exact.sort(key=lambda x: (-float(x["score"]), x.get("date") or "", x["chunk_id"]))
    near.sort(key=lambda x: (-float(x["score"]), x.get("date") or "", x["chunk_id"]))

    # 两类各自保留配额，避免 exact 占满后看不到相近结果
    exact_cap = max(1, min(limit, 40))
    near_cap = max(1, min(limit, 30))
    exact = exact[:exact_cap]
    near = near[:near_cap]
    items = exact + near
    return {
        "query": q,
        "exact": exact,
        "near": near,
        "items": items,
        "total": len(items),
        "exact_total": len(exact),
        "near_total": len(near),
    }


def list_entities(entity_type: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """entity_type: person | place | org"""
    et = (entity_type or "").strip().lower()
    if et not in {"person", "place", "org"}:
        raise ValueError("entity_type 须为 person / place / org")
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT name,
                   COUNT(DISTINCT chunk_id) AS df,
                   SUM(tf) AS total_tf
            FROM chunk_entity
            WHERE entity_type = ?
            GROUP BY name
            ORDER BY df DESC, total_tf DESC, name ASC
            LIMIT ?
            """,
            (et, max(1, min(limit, 500))),
        ).fetchall()
        return [
            {
                "name": r["name"],
                "entity_type": et,
                "df": int(r["df"] or 0),
                "total_tf": int(r["total_tf"] or 0),
            }
            for r in rows
        ]
    finally:
        conn.close()


def _parse_json_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if not s:
        return []
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return [s]


def list_other_tags(*, limit: int = 200) -> dict[str, list[dict[str, Any]]]:
    """聚合 topics / activities / emotions 等其它 tag。"""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT topics, activities, emotions, food_mentions
            FROM chunk_tags
            """
        ).fetchall()
    finally:
        conn.close()

    buckets: dict[str, dict[str, int]] = {
        "topics": {},
        "activities": {},
        "emotions": {},
        "food_mentions": {},
    }
    for r in rows:
        for key in buckets:
            for term in _parse_json_list(r[key] if key in r.keys() else None):
                buckets[key][term] = buckets[key].get(term, 0) + 1

    def top(counter: dict[str, int]) -> list[dict[str, Any]]:
        items = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
        return [{"name": k, "count": v} for k, v in items[:limit]]

    return {k: top(v) for k, v in buckets.items()}


def entity_chunks(
    name: str, entity_type: str, *, limit: int = 30
) -> list[dict[str, Any]]:
    et = (entity_type or "").strip().lower()
    nm = (name or "").strip()
    if et not in {"person", "place", "org"} or not nm:
        raise ValueError("需要有效的 name 与 entity_type")
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.date, c.text, c.source_file, e.tf
            FROM chunk_entity e
            JOIN chunks c ON c.id = e.chunk_id
            WHERE e.name = ? AND e.entity_type = ?
            ORDER BY c.date DESC, c.chunk_index ASC
            LIMIT ?
            """,
            (nm, et, max(1, min(limit, 100))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            text = str(r["text"] or "")
            preview = text.replace("\n", " ").strip()
            if len(preview) > 160:
                preview = preview[:160] + "…"
            out.append(
                {
                    "chunk_id": r["id"],
                    "date": r["date"],
                    "source_file": r["source_file"] or "",
                    "preview": preview,
                    "tf": int(r["tf"] or 0),
                }
            )
        return out
    finally:
        conn.close()
