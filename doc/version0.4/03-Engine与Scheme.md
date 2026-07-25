# v0.4 Engine 与 Scheme

## Candidate

`unit_id` = `rag_sentences.id`（如 `{chunk_id}_s0`）。

## Operators

| name | 行为 |
|------|------|
| embedding | ANN on `diary_sentences` |
| tag | `tag_match` 得 chunk → 展开该 chunk 下全部 sentence（同分） |

**已移除**：`view`

## Schemes

保留：`weighted_50_50`、`union_max`、`tag_only`、`embedding_only`。

废弃（config 删除）：`tag_view_weighted`、`view_only`、`triple_max`。

## hydrate

返回 sentence 正文 + `chunk_id` + `evidence_text`（父 chunk 原文）。
