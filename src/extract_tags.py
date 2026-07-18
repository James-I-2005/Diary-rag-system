"""结构化标签提取。"""

from __future__ import annotations

import json

from src.llm import get_llm_client, get_llm_model
from src.store import get_db

TAG_SCHEMA = {
    "topics": [],
    "activities": [],
    "emotions": [],
    "food_mentions": [],
    "people": [],
    "is_touching_moment": False,
    "touching_summary": "",
}

EXTRACT_PROMPT = """分析以下日记片段，提取结构化信息。

日记内容：
---
{text}
---

日期：{date}

请严格返回 JSON，不要其他文字：
{{
  "topics": ["主题1", "主题2"],
  "activities": ["活动1"],
  "emotions": ["情绪1"],
  "food_mentions": ["食物1"],
  "people": ["人物1"],
  "is_touching_moment": false,
  "touching_summary": ""
}}

规则：
- topics 从以下选：吃饭、运动、工作、学习、朋友、家人、旅行、娱乐、健康、其他
- is_touching_moment：是否包含让人感动、温暖、落泪、心里一暖等时刻
- touching_summary：仅 is_touching_moment 为 true 时填写，一句话概括
- 没有相关内容则对应字段为空数组或 false
"""


def extract_tags_for_chunk(text: str, date: str) -> dict:
    """调用 llm.tags（默认 OpenRouter Gemini）提取单条 chunk 的结构化标签。"""
    client = get_llm_client("tags")
    prompt = EXTRACT_PROMPT.format(text=text, date=date)

    kwargs = {
        "model": get_llm_model("tags"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    try:
        response = client.chat.completions.create(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except Exception:
        # 部分后端不支持 JSON mode
        response = client.chat.completions.create(**kwargs)

    raw = response.choices[0].message.content or "{}"
    # 容错：去掉可能的 markdown 代码围栏
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        )
    return json.loads(raw)


def save_tags(chunk_id: str, tags: dict, conn) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO chunk_tags
           (chunk_id, topics, activities, emotions, food_mentions,
            people, is_touching_moment, touching_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            chunk_id,
            json.dumps(tags.get("topics", []), ensure_ascii=False),
            json.dumps(tags.get("activities", []), ensure_ascii=False),
            json.dumps(tags.get("emotions", []), ensure_ascii=False),
            json.dumps(tags.get("food_mentions", []), ensure_ascii=False),
            json.dumps(tags.get("people", []), ensure_ascii=False),
            1 if tags.get("is_touching_moment") else 0,
            tags.get("touching_summary", ""),
        ),
    )


def _extract_rows(rows, conn) -> None:
    print(f"待提取: {len(rows)} 个 chunk")
    for i, row in enumerate(rows):
        print(f"  [{i + 1}/{len(rows)}] {row['id']} ...")
        try:
            tags = extract_tags_for_chunk(row["text"], row["date"])
            save_tags(row["id"], tags, conn)
            conn.commit()
        except Exception as e:
            print(f"    失败: {e}")


def extract_all_tags() -> None:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT c.id, c.date, c.text
        FROM chunks c
        LEFT JOIN chunk_tags t ON c.id = t.chunk_id
        WHERE t.chunk_id IS NULL
        """
    ).fetchall()
    _extract_rows(rows, conn)
    conn.close()
    print("标签提取完成")


def extract_tags_for_ids(chunk_ids: list[str]) -> None:
    """对指定 chunk id 批量提取（或覆盖）标签。"""
    if not chunk_ids:
        print("没有 chunk 需要提取标签")
        return

    conn = get_db()
    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"""
        SELECT id, date, text FROM chunks
        WHERE id IN ({placeholders})
        """,
        chunk_ids,
    ).fetchall()
    _extract_rows(rows, conn)
    conn.close()
    print("标签提取完成")


if __name__ == "__main__":
    extract_all_tags()
