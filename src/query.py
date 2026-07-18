"""查询：v0.1 统一召回（tag 评分 + 向量并联）；旧路由函数降级保留。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

from src.embed import search_similar
from src.store import get_db
from src.tag_retrieve import (
    extract_query_tags,
    merge_vector_and_tag,
    resolve_retrieval_config,
    tag_match,
)


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


def retrieve_chunks(
    question: str,
    top_k: int | None = None,
    *,
    use_vector: bool = True,
) -> list[dict]:
    """
    v0.1 主召回：tag_match（entity 高权重 + keyword 计数）∪ 向量检索，加权合并。
    """
    retrieval_cfg = resolve_retrieval_config()
    if top_k is not None:
        retrieval_cfg.top_k = top_k

    qside = extract_query_tags(question)
    tag_hits = tag_match(question, cfg=retrieval_cfg.tag_score, query_side=qside)

    vec_hits: list[dict] = []
    if use_vector:
        try:
            vec_hits = search_similar(question, top_k=retrieval_cfg.top_k)
        except Exception as exc:
            print(f"  [warn] 向量检索失败，仅用 tag 路: {exc}")

    if use_vector and vec_hits:
        return merge_vector_and_tag(vec_hits, tag_hits, retrieval_cfg=retrieval_cfg)
    return tag_hits[: retrieval_cfg.top_k]


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


def query(question: str, *, use_vector: bool = True) -> dict:
    """
    v0.1 统一查询入口：一律走 retrieve_chunks。
    旧 statistical / summarization 路由不再作为默认分支。
    """
    qside = extract_query_tags(question)
    chunks = retrieve_chunks(question, use_vector=use_vector)
    return {
        "type": "retrieval",
        "count": len(chunks),
        "chunks": chunks,
        "query_tags": {
            "entities": qside.entities,
            "keywords": qside.keywords,
            "people": qside.people,
            "places": qside.places,
            "orgs": qside.orgs,
        },
    }


if __name__ == "__main__":
    tests = [
        "碧蓮做了什么",
        "中秋節去天壇",
        "討論水泥定價",
    ]
    for q in tests:
        result = query(q, use_vector=False)
        print(f"\nQ: {q}")
        print(f"query_tags: {result['query_tags']}")
        print(f"召回: {result['count']} 条")
        for c in result["chunks"][:3]:
            print(
                f"  [{c.get('date')}] score={c.get('score'):.2f} "
                f"ent={c.get('entity_hits')} kw={c.get('keyword_hits')}"
            )
