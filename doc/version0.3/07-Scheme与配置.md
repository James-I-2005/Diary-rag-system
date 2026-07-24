# v0.3 Scheme 与配置

## 1. 新增内置 Scheme

| id | operators | merge | 权重 | 场景 |
|----|-----------|-------|------|------|
| `weighted_50_50` | tag, embedding | weighted | 0.5/0.5 | v0.2 默认，保留 |
| `tag_view_weighted` | tag, view | weighted | 0.5/0.5 | **v0.3 推荐默认** |
| `view_only` | view | max | — | 抽象成长/价值观 |
| `embedding_only` | embedding | max | — | 保留 |
| `tag_only` | tag | max | — | 保留 |
| `triple_max` | tag, embedding, view | max | — | 全路召回调试 |

## 2. 三路加权（triple weighted，可选）

config 可扩展 `w_view`；v0.3 首版 triple 用 **max 并集**，weighted 仅 tag+view 与 tag+embedding。

## 3. config.yaml 示例

```yaml
retrieval:
  scheme: "weighted_50_50"   # 初期不变；验收后改 tag_view_weighted
  schemes:
    tag_view_weighted:
      label: "Tag + View 加权 (0.5/0.5)"
      description: "实体关键词 + Memory View 语义"
      operators: ["tag", "view"]
      merge: weighted
      w_tag: 0.5
      w_view: 0.5
    view_only:
      label: "仅 Memory View"
      operators: ["view"]
      merge: max
    triple_max:
      label: "Tag + RAG + View 并集"
      operators: ["tag", "embedding", "view"]
      merge: max

memory_views:
  max_views_per_chunk: 6
  view_ann_multiplier: 3   # view_top_k = top_k * multiplier
```

## 4. 环境变量

| 变量 | 说明 |
|------|------|
| `RETRIEVAL_SCHEME` | 覆盖默认 scheme |
| `MEMORY_VIEWS_ENABLED` | 全局禁用 view 算子（回滚） |

## 5. run_scheme 扩展

```python
run_scheme(
    query: str,
    scheme: RetrievalScheme | str | None = None,
    *,
    structured: StructuredQuery | None = None,
    top_k: int | None = None,
) -> tuple[list[Candidate], RetrievalScheme]
```

ViewOperator 通过 `structured` 获取 embedding_query 与 filters。

## 6. Web UI

顶栏 scheme 下拉来自 `list_schemes()`，新增 scheme 自动可见，无需改前端。

## 7. 源码

- `src/engine/schemes.py`
- `src/engine/candidate.py`（merge_candidates_weighted 扩展 w_view）
- `config.yaml`
