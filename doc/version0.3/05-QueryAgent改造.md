# v0.3 Query Agent 改造

## 1. 职责边界

Query Agent 产出：

- **是否检索**（need_retrieval）
- **意图**（intent）
- **Tag 检索句**（rewritten_query）
- **View 检索表示**（query_representation + embedding_query）

**不决定** Operator 组合；Scheme 决定 `tag / view / embedding`。

## 2. StructuredQuery v0.3

```python
@dataclass
class QueryRepresentation:
    semantic_facets: list[str] = field(default_factory=list)
    view_type_hints: list[str] = field(default_factory=list)
    time_range: dict[str, str] | None = None  # {"start": "...", "end": "..."}
    entity_hints: list[str] = field(default_factory=list)

@dataclass
class StructuredQuery:
    original_query: str
    rewritten_query: str
    need_retrieval: bool = True
    intent: Intent = "unknown"
    retrieval_plan: list[str] = field(default_factory=list)
    query_representation: QueryRepresentation | None = None
    embedding_query: str = ""
    source: str = "llm"
    meta: dict[str, Any] = field(default_factory=dict)
```

### 方法

- `retrieval_query()` → rewritten_query（TagOperator）
- `view_retrieval_query()` → embedding_query 或 rewritten_query

## 3. LLM 输出 JSON（单次调用）

在 need_retrieval=true 时扩展字段：

```json
{
  "need_retrieval": true,
  "intent": "summary",
  "rewritten_query": "归纳与长期主义、习惯养成相关的经历",
  "embedding_query": "寻找与长期投入、稳定习惯、接受成长过程、价值观形成相关的记忆",
  "query_representation": {
    "semantic_facets": ["长期投入", "习惯建立", "接受成长过程"],
    "view_type_hints": ["growth", "identity", "future_query"],
    "time_range": null,
    "entity_hints": []
  }
}
```

conversation 时仅输出路由字段，representation 可省略。

## 4. intent → view_type_hints 默认映射

Query Agent 未给出 hints 时，代码侧 fallback：

| intent | 默认 hints |
|--------|------------|
| memory_recall | event, narrative |
| memory_search | event, future_query |
| summary | growth, identity, event |
| unknown | （不过滤） |
| conversation | （不检索） |

## 5. 硬校验

- `conversation → need_retrieval=false`
- 其他 intent → need_retrieval=true
- `embedding_query` 空 → 用 rewritten_query
- `view_type_hints` 非法值过滤
- `semantic_facets` 最多 8 条

## 6. ContextService 传参

`_retrieve(structured: StructuredQuery, ...)` → `run_scheme(..., structured=structured)`

## 7. 源码

- `src/query_agent/models.py`
- `src/query_agent/agent.py`

## 8. 调试

`data/last_query.json` 含完整 `query_representation` 与 `embedding_query`。

```powershell
python -m src.query_agent
```
