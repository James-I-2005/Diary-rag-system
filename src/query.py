"""查询：v0.2 Engine Plan 召回 + hydrate；旧路由函数降级保留。"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.embed import search_similar
from src.engine import PlanExecutor, build_plan, build_plan_from_config
from src.store import get_db, load_config, resolve_path
from src.tag_retrieve import extract_query_tags, resolve_retrieval_config


def classify_query(question: str) -> str:
    q = question.strip()

    statistical_signals = ["多少", "几次", "多少次", "有没有", "统计", "计数", "总共"]
    if any(s in q for s in statistical_signals):
        return "statistical"

    summarization_signals = ["最喜欢", "最常", "总结", "归纳", "主要", "偏好", "习惯"]
    if any(s in q for s in summarization_signals):
        return "summarization"

    return "retrieval"


def infer_tag_filters(question: str) -> dict:
    """从问题推断标签过滤条件。（v0 遗留，主路径不再使用）"""
    filters: dict = {}
    if any(w in question for w in ["吃饭", "吃", "美食", "餐厅", "食物"]):
        filters["has_food"] = True
    if any(w in question for w in ["感动", "温暖", "落泪"]):
        filters["is_touching_moment"] = True
    if any(w in question for w in ["朋友", "聚会", "社交"]):
        filters["topics_contains"] = "朋友"
    return filters


def filter_by_tags(filters: dict) -> list[dict]:
    conn = get_db()
    conditions: list[str] = []
    params: list = []

    if filters.get("has_food"):
        conditions.append("t.food_mentions != '[]'")
    if filters.get("is_touching_moment"):
        conditions.append("t.is_touching_moment = 1")
    if filters.get("topics_contains"):
        conditions.append("t.topics LIKE ?")
        params.append(f'%"{filters["topics_contains"]}"%')

    where = " AND ".join(conditions) if conditions else "1=1"
    rows = conn.execute(
        f"""
        SELECT c.id, c.date, c.text, t.topics, t.food_mentions
        FROM chunks c
        JOIN chunk_tags t ON c.id = t.chunk_id
        WHERE {where}
        ORDER BY c.date
        """,
        params,
    ).fetchall()
    conn.close()

    return [{"id": r["id"], "date": r["date"], "text": r["text"]} for r in rows]


# 句子池相对最终 chunk top_k 的倍率，避免同 chunk 多句挤占名额后 chunk 数不足
_SENTENCE_POOL_MULTIPLIER = 3


def sentence_pool_size(chunk_top_k: int) -> int:
    """Operator 侧多取一些句子，留给 hydrate 按 chunk 聚合。"""
    k = max(int(chunk_top_k), 1)
    return max(k * _SENTENCE_POOL_MULTIPLIER, k)


def hydrate_candidates(candidates: list, *, top_k: int | None = None) -> list[dict]:
    """
    sentence 候选 → 按父 chunk 聚合为召回单位。

    - 匹配基元仍是 rag-sentence；任一句子命中即带上整个 chunk
    - 每条输出：text=chunk 全文，score=组内最高分，matched_sentences=命中理由
    - top_k 按 chunk 计数（去重后）
    """
    if not candidates:
        return []
    retrieval_cfg = resolve_retrieval_config()
    k = top_k if top_k is not None else retrieval_cfg.top_k

    ids = [c.unit_id for c in candidates if getattr(c, "unit_id", None)]
    if not ids:
        return []

    conn = get_db()
    try:
        placeholders = ",".join("?" * len(ids))
        sent_rows = {
            r["id"]: r
            for r in conn.execute(
                f"""SELECT id, chunk_id, text, date FROM rag_sentences
                    WHERE id IN ({placeholders})""",
                ids,
            ).fetchall()
        }
        # fallback：unit_id 可能是未 paraphrase 的 chunk_id
        missing = [i for i in ids if i not in sent_rows]
        chunk_fallback: dict = {}
        if missing:
            ph2 = ",".join("?" * len(missing))
            chunk_fallback = {
                r["id"]: r
                for r in conn.execute(
                    f"SELECT id, date, text FROM chunks WHERE id IN ({ph2})",
                    missing,
                ).fetchall()
            }
        parent_ids = list(
            {
                *(r["chunk_id"] for r in sent_rows.values()),
                *chunk_fallback.keys(),
            }
        )
        chunk_texts: dict[str, str] = {}
        chunk_dates: dict[str, str] = {}
        if parent_ids:
            ph3 = ",".join("?" * len(parent_ids))
            for r in conn.execute(
                f"SELECT id, date, text FROM chunks WHERE id IN ({ph3})",
                parent_ids,
            ).fetchall():
                chunk_texts[r["id"]] = r["text"] or ""
                chunk_dates[r["id"]] = r["date"] or ""
        for cid, row in chunk_fallback.items():
            chunk_texts.setdefault(cid, row["text"] or "")
            chunk_dates.setdefault(cid, row["date"] or "")
    finally:
        conn.close()

    # chunk_id → 聚合状态
    groups: dict[str, dict] = {}
    for c in candidates:
        uid = getattr(c, "unit_id", None)
        if not uid:
            continue
        score = float(getattr(c, "score", 0.0) or 0.0)
        source = str(getattr(c, "source", "") or "")
        sent = sent_rows.get(uid)
        if sent:
            parent = sent["chunk_id"]
            hit = {
                "id": uid,
                "text": sent["text"] or "",
                "score": score,
                "source": source,
            }
            date = sent["date"] or chunk_dates.get(parent, "")
        elif uid in chunk_fallback:
            parent = uid
            # 无 paraphrase：整段 chunk 既是匹配也是正文，不单独列命中句
            hit = None
            date = chunk_fallback[uid]["date"] or chunk_dates.get(parent, "")
        else:
            continue

        g = groups.get(parent)
        if g is None:
            groups[parent] = {
                "chunk_id": parent,
                "score": score,
                "source": source,
                "date": date,
                "matched_sentences": [hit] if hit else [],
            }
            continue

        if score > g["score"]:
            g["score"] = score
        if source and source not in g["source"].split("+"):
            parts = [p for p in g["source"].split("+") if p]
            if source not in parts:
                parts.append(source)
            g["source"] = "+".join(parts) if parts else source
        if not g["date"] and date:
            g["date"] = date
        if hit:
            # 同句重复命中：保留更高分
            existing = next(
                (s for s in g["matched_sentences"] if s["id"] == hit["id"]),
                None,
            )
            if existing is None:
                g["matched_sentences"].append(hit)
            elif hit["score"] > existing["score"]:
                existing["score"] = hit["score"]
                if hit["source"]:
                    existing["source"] = hit["source"]

    ranked = sorted(
        groups.values(),
        key=lambda g: (-float(g["score"]), g.get("date") or "", g["chunk_id"]),
    )[:k]

    out: list[dict] = []
    for g in ranked:
        cid = g["chunk_id"]
        full = chunk_texts.get(cid, "")
        hits = sorted(
            g["matched_sentences"],
            key=lambda s: (-float(s["score"]), s["id"]),
        )
        out.append(
            {
                "id": cid,
                "unit_id": cid,
                "chunk_id": cid,
                "date": g["date"] or chunk_dates.get(cid, ""),
                "text": full,
                "score": float(g["score"]),
                "source": g["source"],
                "matched_sentences": hits,
                # 兼容旧字段：正文即 chunk，证据与 text 相同
                "evidence_text": full,
            }
        )
    return out


def retrieve_chunks(
    question: str,
    top_k: int | None = None,
    *,
    use_vector: bool = True,
    plan_names: list[str] | None = None,
) -> list[dict]:
    """
    主召回：Operator 在 sentence 上匹配 → hydrate 按 chunk 聚合。
    use_vector=False 时强制仅跑 tag（便于无 Chroma 时试跑）。
    """
    retrieval_cfg = resolve_retrieval_config()
    k = top_k if top_k is not None else retrieval_cfg.top_k
    pool = sentence_pool_size(k)

    if plan_names is not None:
        names = plan_names
    elif not use_vector:
        names = ["tag"]
    else:
        names = None  # 走 config

    plan = build_plan(names, top_k=pool) if names is not None else build_plan_from_config(top_k=pool)
    candidates = PlanExecutor().run(question, plan)
    return hydrate_candidates(candidates, top_k=k)


def statistical_query(question: str) -> dict:
    """统计型：SQL 聚合，返回数字 + 明细。（v0 遗留）"""
    conn = get_db()

    if any(w in question for w in ["感动", "温暖"]):
        count = conn.execute(
            "SELECT COUNT(*) FROM chunk_tags WHERE is_touching_moment = 1"
        ).fetchone()[0]
        details = conn.execute(
            """
            SELECT c.date, c.text, t.touching_summary
            FROM chunk_tags t
            JOIN chunks c ON c.id = t.chunk_id
            WHERE t.is_touching_moment = 1
            ORDER BY c.date
            """
        ).fetchall()
        conn.close()
        return {
            "type": "statistical",
            "metric": "touching_moments",
            "count": count,
            "details": [dict(d) for d in details],
        }

    if any(w in question for w in ["火锅"]):
        rows = conn.execute(
            """
            SELECT c.id, c.date, c.text, t.food_mentions
            FROM chunk_tags t
            JOIN chunks c ON c.id = t.chunk_id
            WHERE t.food_mentions LIKE '%火锅%'
            """
        ).fetchall()
        conn.close()
        return {
            "type": "statistical",
            "metric": "hotpot_mentions",
            "count": len(rows),
            "details": [dict(r) for r in rows],
        }

    conn.close()
    return {"type": "statistical", "error": "暂不支持该统计问题"}


def summarization_query(question: str) -> dict:
    """归纳型：聚合标签频次 + 取向量相关片段。（v0 遗留）"""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT activities, emotions, topics FROM chunk_tags
        """
    ).fetchall()
    conn.close()

    activity_counter: Counter = Counter()
    for row in rows:
        activities = json.loads(row["activities"] or "[]")
        activity_counter.update(activities)

    top_activities = activity_counter.most_common(5)

    evidence = {}
    for activity, _ in top_activities[:3]:
        chunks = search_similar(activity, top_k=3)
        evidence[activity] = chunks

    return {
        "type": "summarization",
        "top_activities": top_activities,
        "evidence": evidence,
        "question": question,
    }


def parse_date_range(question: str) -> tuple[str, str] | None:
    """解析「这周」「这个月」等相对日期（扩展，主流程可选接入）。"""
    today = datetime.now().date()
    if "这周" in question or "本周" in question:
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    if "这个月" in question or "本月" in question:
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat()
    return None


def save_retrieval_json(result: dict, question: str) -> Path | None:
    """
    把本次召回结果写入 JSON（默认 data/last_retrieval.json）。
    若配置了 history_dir，再额外存一份带时间戳的副本。
    """
    cfg = load_config().get("retrieval") or {}
    enabled = os.getenv("RETRIEVAL_SAVE_JSON", "").strip().lower()
    if enabled:
        save = enabled in {"1", "true", "yes", "on"}
    else:
        save = bool(cfg.get("save_json", True))
    if not save:
        return None

    payload = {
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "question": question,
        **result,
    }
    # chunks 正文可能很长：审阅文件保留全文；如需可后续加 truncate

    log_rel = (
        os.getenv("RETRIEVAL_LOG_PATH", "").strip()
        or cfg.get("log_path")
        or "data/last_retrieval.json"
    )
    out = resolve_path(log_rel)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    hist_rel = (
        os.getenv("RETRIEVAL_HISTORY_DIR", "").strip()
        or cfg.get("history_dir")
        or ""
    )
    if hist_rel:
        hist_dir = resolve_path(hist_rel)
        hist_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_q = "".join(c if c.isalnum() or c in "-_" else "_" for c in question[:24])
        hist_path = hist_dir / f"{stamp}_{safe_q or 'q'}.json"
        hist_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return out


def query(
    question: str,
    *,
    use_vector: bool = True,
    plan_names: list[str] | None = None,
    save_json: bool | None = None,
) -> dict:
    """
    v0.2 统一查询入口：Engine Plan → hydrate → 可选写入 JSON。
    """
    qside = extract_query_tags(question)
    if plan_names is not None:
        names = plan_names
    elif not use_vector:
        names = ["tag"]
    else:
        names = None

    chunks = retrieve_chunks(question, use_vector=use_vector, plan_names=names)
    plan = (
        build_plan(names)
        if names is not None
        else build_plan_from_config()
    )
    result = {
        "type": "retrieval",
        "count": len(chunks),
        "chunks": chunks,
        "plan": [op.name for op in plan.operators],
        "query_tags": {
            "entities": qside.entities,
            "keywords": qside.keywords,
            "people": qside.people,
            "places": qside.places,
            "orgs": qside.orgs,
        },
    }
    if save_json is not False:
        path = save_retrieval_json(result, question)
        if path is not None:
            result["log_path"] = str(path)
    return result


if __name__ == "__main__":
    tests = [
        "碧蓮做了什么",
        "討論水泥定價",
    ]
    for q in tests:
        result = query(q, use_vector=False)
        print(f"\nQ: {q}")
        print(f"plan: {result['plan']}")
        print(f"query_tags: {result['query_tags']}")
        print(f"召回: {result['count']} 条")
        if result.get("log_path"):
            print(f"JSON → {result['log_path']}")
        for c in result["chunks"][:3]:
            print(
                f"  [{c.get('date')}] score={c.get('score'):.2f} "
                f"source={c.get('source')} id={c.get('id')}"
            )
