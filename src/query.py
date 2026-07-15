"""查询路由：分类 → 执行 → 返回结构化结果。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

from src.embed import search_similar
from src.store import get_db


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
    """从问题推断标签过滤条件。"""
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


def retrieve_chunks(question: str, top_k: int = 20) -> list[dict]:
    """检索型：标签过滤 + 向量检索，合并去重。"""
    results_by_id: dict = {}

    tag_filters = infer_tag_filters(question)
    if tag_filters:
        sql_results = filter_by_tags(tag_filters)
        for r in sql_results:
            results_by_id[r["id"]] = r

    vec_results = search_similar(question, top_k=top_k)
    for r in vec_results:
        if r["id"] not in results_by_id:
            results_by_id[r["id"]] = r
        else:
            results_by_id[r["id"]]["score"] = r.get("score", 0)

    merged = sorted(results_by_id.values(), key=lambda x: x.get("date", ""))
    return merged


def statistical_query(question: str) -> dict:
    """统计型：SQL 聚合，返回数字 + 明细。"""
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
    """归纳型：聚合标签频次 + 取向量相关片段。"""
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


def query(question: str) -> dict:
    """统一查询入口。"""
    qtype = classify_query(question)

    if qtype == "statistical":
        return statistical_query(question)
    if qtype == "summarization":
        return summarization_query(question)

    chunks = retrieve_chunks(question)
    return {
        "type": "retrieval",
        "count": len(chunks),
        "chunks": chunks,
    }


if __name__ == "__main__":
    tests = [
        "这个月所有关于吃饭的内容",
        "感动瞬间有多少次",
        "这个月最喜欢做的事情是什么",
    ]
    for q in tests:
        result = query(q)
        print(f"\nQ: {q}")
        print(f"类型: {result['type']}")
        if result["type"] == "statistical":
            print(f"计数: {result.get('count')}")
        elif result["type"] == "retrieval":
            print(f"召回: {result['count']} 条")
        elif result["type"] == "summarization":
            print(f"Top活动: {result.get('top_activities', [])[:3]}")
