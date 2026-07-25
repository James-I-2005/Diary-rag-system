# v0.4 rag-sentence 架构

## 决策

- **检索基元**：`rag-sentence`（自包含、低指代的自然语言句）
- **Chunk 降级**：仅作 500 字切分中间态 + Context 证据父节点
- **View 退役**：`memory_views` / `diary_views` / `ViewOperator` 不再主路径（v0.3 废弃）

Prompt 定义见仓库根目录 `paraphrase/prompt/rag-sentence.md`，My_rag 内副本：`src/paraphrase/prompt_rag_sentence.md`。

## 数据流

```text
Diary → Chunk(500) → Paraphrase → rag_sentences → Chroma diary_sentences
User → QueryAgent → Engine(tag|embedding on sentences) → hydrate → Context → LLM
```

## 与 v0.3 对比

| | v0.3 | v0.4 |
|--|------|------|
| ANN 对象 | Memory View / chunk | **rag-sentence** |
| Candidate.id | chunk_id | **unit_id = sentence_id** |
| Tag | chunk 打分 | chunk 打分后**展开**为 sentences |
| Context | views + 原文 | **sentence + 父 chunk 证据** |
