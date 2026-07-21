# Query Agent Architecture v0.1

## Overview

Query Agent 是整个 Memory System 的入口。

它位于 Memory Engine 之前。

整体流程：

```
                User Query
                     |
                     v
               Query Agent
                     |
        +------------+------------+
        |                         |
        |                         |
 Normal Conversation       Memory Retrieval
        |                         |
        |                         |
        |                  Retrieval Plan
        |                         |
        +------------+------------+
                     |
                     v
               Context Engine
                     |
                     v
                    LLM
```

Query Agent 不负责：

- Memory Retrieval
- Context Construction
- LLM 回答

它的职责只有一个：

> 理解用户真正的意图，并决定系统下一步应该如何工作。

---

# Design Principle

Query Agent 应该尽可能早地完成：

- Query 理解
- Query 重写
- Retrieval 决策

后续所有模块都应尽量处理结构化信息，而不是原始自然语言。

因此：

```
Raw User Query

↓

Query Agent

↓

Structured Query

↓

Memory Engine
```

---

# Responsibilities

当前版本 Query Agent 包含三个能力：

```
1. Query Understanding

2. Query Rewriting

3. Retrieval Planning（预留）
```

---

# Processing Pipeline

整体流程：

```
Raw User Query

        |

        v

Query Understanding

        |

        v

Query Rewriting

        |

        v

Retrieval Planning

        |

        v

Structured Query
```

---

# 1. Query Understanding

## Goal

理解用户当前真正想做什么。

不是所有输入都需要 Memory Retrieval。

例如：

```
你好
```

```
谢谢
```

```
哈哈哈哈
```

```
你是谁？
```

这些属于普通聊天。

无需进入 Memory Engine。

而：

```
我第一次去东京是什么时候？
```

```
去年夏天发生了什么？
```

```
我有没有写过关于焦虑的内容？
```

属于 Memory Retrieval。

需要进入 Retrieval Pipeline。

---

## Output

Query Understanding 至少需要判断：

```
是否需要检索（Need Retrieval）

当前 Query 类型（Intent）
```

例如：

```
Need Retrieval

True / False
```

如果：

```
False
```

系统直接进入：

```
Context Engine

↓

LLM
```

跳过 Memory Engine。

---

## Intent（当前版本可简单分类）

例如：

```
Conversation

Memory Recall

Memory Search

Summary

Unknown
```

未来可以继续增加：

```
Timeline

Person

Emotion

Location

Relationship

Comparison

Reasoning

...
```

---

# Query Understanding Prompt

建议采用固定 System Prompt。

Prompt 的目标：

不是回答用户问题。

而是：

判断：

```
是否需要 Retrieval
```

输出结构化结果。

例如：

```json
{
    "need_retrieval": true,
    "intent": "memory_recall"
}
```

---

# 2. Query Rewriting

## Goal

将用户自然语言转换成更适合：

Memory Retrieval

和

LLM 理解

的形式。

---

例如：

用户：

```
那次东京旅行怎么样？
```

重写：

```
用户询问东京旅行相关经历，
重点关注旅行内容。
```

---

例如：

用户：

```
后来呢？
```

如果结合 Conversation：

可以重写：

```
继续讨论2019年东京旅行。
```

---

例如：

用户：

```
我第一次认识A是什么时候？
```

重写：

```
查询：
用户第一次认识人物A的时间和相关事件。
```

---

## Design Principle

Query Rewriting：

只改变表达。

不改变语义。

不能添加：

模型猜测。

不能创造不存在的信息。

---

## Output

输出：

```
Rewritten Query
```

后续：

Memory Engine

使用：

```
Rewritten Query
```

进行 Retrieval。

而不是：

原始 Query。

---

# 3. Retrieval Planning

当前版本：

仅预留接口。

暂不实现复杂规划。

---

未来职责：

根据：

```
Intent

Query Type

Conversation Context
```

自动生成：

```
Retrieval Plan
```

例如：

```
Embedding

↓

Tag
```

或者：

```
Tag

↓

Embedding
```

未来：

```
Graph

↓

Timeline

↓

Embedding
```

均由 Planner 决定。

---

当前实现：

默认：

```
Tag

↓

Embedding
```

即可。

---

# Structured Query

Query Agent 最终输出统一结构。

例如：

```json
{
    "original_query": "...",

    "rewritten_query": "...",

    "need_retrieval": true,

    "intent": "memory_recall",

    "retrieval_plan": [
        "tag",
        "embedding"
    ]
}
```

Memory Engine 不直接处理原始 Query。

统一处理：

```
Structured Query
```

---

# Failure Handling

如果：

Query Agent 无法判断：

```
Need Retrieval
```

默认：

```
True
```

原因：

Memory Retrieval 的成本通常低于：

遗漏一次真正需要 Retrieval 的请求。

采用：

Recall 优先。

---

# Future Extensions

未来可扩展：

---

## Entity Extraction

自动提取：

```
人物

时间

地点

事件

标签
```

例如：

```
东京

朋友A

2020

摄影
```

供 Retrieval 使用。

---

## Query Decomposition

复杂问题：

```
为什么后来我越来越喜欢摄影？
```

拆分：

```
第一次接触摄影

↓

摄影相关事件

↓

时间线

↓

总结原因
```

未来可支持多步 Retrieval。

---

## Dynamic Planner

根据 Query 自动生成：

```
Operator Pipeline
```

例如：

```
Graph

↓

Timeline

↓

Embedding
```

无需人工配置。

---

## Planner Learning

根据 Retrieval Feedback：

自动优化：

```
Plan Selection
```

实现：

Learning-based Planner。

---

# Current Scope

当前版本实现：

✓ Query Understanding

✓ Query Rewriting

✓ Need Retrieval 判断

✓ Intent Classification

✓ Structured Query Output

✓ Retrieval Plan 预留

暂不实现：

✗ Entity Extraction

✗ Query Decomposition

✗ Dynamic Planner

✗ Plan Optimization

✗ Self Learning

目标是建立一个稳定、可扩展的 Query Agent，使其成为整个 Memory Runtime 的统一入口，并为未来增加复杂 Retrieval Planning 能力预留扩展空间。