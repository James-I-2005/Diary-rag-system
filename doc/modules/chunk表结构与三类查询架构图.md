# 三类查询 · 函数签名与输入输出示例

基于 `data/diary/sample.md`。每个函数只举 1 个例子。

---

## 库中数据（节选）

| chunks.id | date | text 节选 |
|-----------|------|-----------|
| `2025-06-15_0` | 2025-06-15 | 吃了火锅…散步看到夕阳，有点感动 |
| `2025-06-16_0` | 2025-06-16 | 加班…吃了便利店饭团 |
| `2025-06-25_0` | 2025-06-25 | 同事改 PPT…心里一暖 |

| chunk_tags.chunk_id | food_mentions | is_touching_moment | activities |
|---------------------|---------------|--------------------|------------|
| `2025-06-15_0` | `["火锅"]` | 1 | `["吃火锅","散步"]` |
| `2025-06-16_0` | `["饭团"]` | 0 | `["加班","吃饭团"]` |
| `2025-06-25_0` | `[]` | 1 | `["改PPT"]` |

---

## 模块 1：路由

### `classify_query(question: str) -> str`

用关键词把问题分成 retrieval / statistical / summarization 三类。

```python
# 输入
question = "感动瞬间有多少次"
# 输出
result = "statistical"
```

### `query(question: str) -> dict`

统一入口：先分类，再调用对应查询函数并返回结构化结果。

```python
# 输入
question = "感动瞬间有多少次"
# 输出
result = {
  "type": "statistical",
  "metric": "touching_moments",
  "count": 2,
  "details": [...],   # 见 statistical_query
}
```

---

## 模块 2：检索型

调用链：`retrieve_chunks` → `infer_tag_filters` → `filter_by_tags`  
　　　　　　　　　└→ `search_similar` → 合并

### `infer_tag_filters(question: str) -> dict`

从问题文本推断标签过滤条件，不读数据库。

```python
# 输入
question = "这个月所有关于吃饭的内容"
# 输出
filters = {"has_food": True}
```

### `filter_by_tags(filters: dict) -> list[dict]`

按标签条件 JOIN 两表，筛出匹配的日记片段。

```python
# 输入
filters = {"has_food": True}
# 输出
chunks = [
  {"id": "2025-06-15_0", "date": "2025-06-15", "text": "…吃了火锅…"},
  {"id": "2025-06-16_0", "date": "2025-06-16", "text": "…吃了便利店饭团…"},
]
```

### `search_similar(query: str, top_k: int | None = None) -> list[dict]`

把查询编成向量，在 Chroma 中找语义最相近的 chunk。

```python
# 输入
query = "这个月所有关于吃饭的内容"
top_k = 20
# 输出
chunks = [
  {"id": "2025-06-15_0", "date": "2025-06-15", "text": "…吃了火锅…", "score": 0.72},
  {"id": "2025-06-16_0", "date": "2025-06-16", "text": "…饭团…", "score": 0.65},
]
```

### `retrieve_chunks(question: str, top_k: int = 20) -> list[dict]`

检索型总装：标签过滤 ∪ 向量检索，按 id 去重后按日期排序。

```python
# 输入
question = "这个月所有关于吃饭的内容"
top_k = 20
# 输出
chunks = [
  {"id": "2025-06-15_0", "date": "2025-06-15", "text": "…吃了火锅…", "score": 0.72},
  {"id": "2025-06-16_0", "date": "2025-06-16", "text": "…饭团…", "score": 0.65},
]
```

---

## 模块 3：统计型

### `statistical_query(question: str) -> dict`

用 SQL 计数并查明细，数字由数据库算出，不靠 LLM 猜测。

```python
# 输入
question = "感动瞬间有多少次"
# 输出
result = {
  "type": "statistical",
  "metric": "touching_moments",
  "count": 2,
  "details": [
    {"date": "2025-06-15", "text": "…有点感动。", "touching_summary": "散步看到夕阳心里一暖"},
    {"date": "2025-06-25", "text": "…心里一暖。", "touching_summary": "同事默默帮忙心里一暖"},
  ],
}
```

---

## 模块 4：归纳型

### `summarization_query(question: str) -> dict`

统计 activities 频次得到 Top 活动，再用向量检索取佐证片段。

```python
# 输入
question = "这个月最喜欢做的事情是什么"
# 输出
result = {
  "type": "summarization",
  "question": "这个月最喜欢做的事情是什么",
  "top_activities": [("吃火锅", 1), ("散步", 1), ("加班", 1), ("吃饭团", 1), ("改PPT", 1)],
  "evidence": {
    "吃火锅": [{"id": "2025-06-15_0", "date": "2025-06-15", "text": "…吃了火锅…", "score": 0.82}],
    "散步":   [{"id": "2025-06-15_0", "date": "2025-06-15", "text": "…散步…", "score": 0.75}],
    "加班":   [{"id": "2025-06-16_0", "date": "2025-06-16", "text": "…加班…", "score": 0.80}],
  },
}
```

（内部：读 `activities` → `json.loads` → `Counter.most_common(5)` → 对前 3 个活动调 `search_similar`。）

---

## 对照

| 函数 | 输入例子 | 输出要点 |
|------|----------|----------|
| `classify_query` | `question=...` | `result="statistical"` |
| `infer_tag_filters` | `question=...` | `filters={"has_food": True}` |
| `filter_by_tags` | `filters={has_food: True}` | `chunks=[...]` |
| `search_similar` | `query=..., top_k=20` | `chunks=[...]` |
| `retrieve_chunks` | `question=..., top_k=20` | `chunks=[...]` |
| `statistical_query` | `question=...` | `result={metric, count, details}` |
| `summarization_query` | `question=...` | `result={top_activities, evidence}` |
| `query` | `question=...` | `result` 三种 `type` 之一 |
