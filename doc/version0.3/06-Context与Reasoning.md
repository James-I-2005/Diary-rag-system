# v0.3 Context 与 Reasoning

## 1. 目标

Context Engine 消费 hydrate 后的 chunk + matched_views，组装 **Reasoning 前置材料**；v0.3 不单独拆 Memory Reasoning Agent，由 Answer LLM 完成综合。

## 2. RetrievedMemory 扩展

```python
@dataclass
class RetrievedMemory:
    chunk_id: str
    score: float = 0.0
    source: str = ""
    date: str = ""
    text: str = ""
    matched_views: list[dict] = field(default_factory=list)
    evidence_text: str = ""   # 默认同 text，预留摘要
```

`from_hydrated` 读取 `matched_views` 字段。

## 3. Prompt 记忆块格式

`_pack_memories` 输出示例：

```text
[2026-07-20] chunk_id=10001 (score=0.82, view)
相关视角：
- [growth] 作者开始接受成长需要时间…
- [identity] 正在形成长期主义价值观…
原文证据：
今天练习英语书法。一开始觉得游丝很难控制…
```

无 matched_views 时退化为 v0.2 单行格式。

## 4. system prompt 补充

在现有陪伴型助手基础上增加：

- Views 是检索到的**语义视角**，原文是**证据**
- 可综合推理；抽象结论需有 view 或原文支撑
- 无记忆时照常闲聊，不因缺材料拒答

## 5. 空 View / 空 chunk

| 情况 | 行为 |
|------|------|
| need_retrieval=false | 不注入记忆块 |
| 检索 0 条 | 无记忆块，LLM 正常陪聊 |
| 有 chunk 无 views | 仅展示原文证据 |

## 6. v0.3.1 预留

若 Answer 质量不足，可加轻量 **LLM Rerank** 节点（多 stage retrieval），不改 Context 块格式。

## 7. 源码

- `src/context/models.py`
- `src/context/engine.py`
- `config.yaml` → `context.system_prompt`
