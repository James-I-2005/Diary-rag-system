# Context Engine Architecture v0.1

## Overview

Context Engine 是连接：

```
User Conversation
        |
        |
Memory Retrieval Engine
        |
        |
LLM
```

之间的中间层。

它的核心职责不是检索记忆，而是：

> 根据当前对话状态、历史上下文、Memory Engine 返回的候选记忆，构建一次 LLM 请求所需要的 Context。

Context Engine 负责决定：

- 哪些历史消息应该进入 Prompt
- 哪些 Memory Retrieval 结果应该进入 Prompt
- 如何压缩过长的上下文
- 如何控制 Token Budget
- 如何组织最终 Prompt 结构


---

# Design Principle

Context Engine 遵循以下原则：

## 1. Conversation 与 Memory 解耦

Conversation 是：

> 用户正在进行的一次交流

Memory 是：

> 用户过去发生过的事情

二者不能混合存储。


例如：

用户当前询问：

```
我第一次去东京是什么时候？
```

Memory Engine 返回：

```
2019年东京旅行
2020年东京出差
2023年东京旅游
```

这些 Retrieval Result 只是当前请求的 Context。

它们不会自动成为 Conversation History。


---

## 2. Retrieved Memory 默认是临时 Context

一次请求流程：

```
User Query

↓

Memory Retrieval

↓

Retrieved Memories

↓

Context Construction

↓

LLM

↓

Assistant Response
```

Retrieved Memory 不应该永久追加到聊天历史。


原因：

如果永久保存：

```
Message 1:

用户：
我什么时候去东京？

Retrieved:
2019东京旅行


Message 20:

用户：
东京有什么好吃的？
```

模型可能错误认为：

当前东京一定指2019年。

因此：

Memory Retrieval 是动态的。

每一轮根据 Query 重新召回。


---

# Overall Flow

一次完整请求流程：


```
                 User Input

                     |
                     v

          Conversation Manager

                     |
                     |
              Current Context
                     |
                     v

          Memory Retrieval Engine

                     |
                     |
          Retrieved Memory Candidates
                     |
                     v

             Context Engine

                     |
                     |
             Final LLM Context

                     |
                     v

                    LLM

                     |
                     v

             Assistant Response

                     |
                     v

          Save Conversation History
```

---

# Context Construction Pipeline


Context Engine 将多个来源的信息组合：

```
System Prompt

+

Conversation Summary

+

Recent Conversation Messages

+

Retrieved Memories

+

Current User Query

```

形成最终发送给 LLM 的 Prompt。


---

# Context Components


## 1. System Context

系统级信息。

例如：

```
你是一个个人记忆助手。

你的任务是帮助用户回忆过去经历。
```

特点：

- 固定
- 不随 Conversation 改变


---

## 2. Conversation Summary

用于保存长期对话状态。


当 Conversation 很长时：

不能：

```
Message 1

Message 2

...

Message 100
```

全部进入 Context。


因此需要维护 Summary。


例如：

原始：

```
User:
我最近在回忆大学时期。

Assistant:
...

User:
尤其想知道和朋友A的事情。
```

压缩为：

```
用户正在回忆大学时期经历，
重点关注朋友A相关事件。
```

Summary 用于保持：

- 对话主题
- 用户当前目标
- 已经讨论过的信息


---

## 3. Recent Messages

保留最近若干轮原始消息。


例如：

```
最近 5~10 轮 conversation
```


作用：

保证模型理解：

- 当前上下文
- 指代关系
- 最近的问题


例如：

```
User:
那之后呢？
```

如果没有最近消息：

模型不知道“那”指什么。


---

## 4. Retrieved Memories

来自 Memory Retrieval Engine。


特点：

临时加入。


例如：

```
Memory 1:

2019年7月15日，
用户第一次去东京旅行，
同行人为朋友A。


Memory 2:

2020年春节，
用户再次访问东京。
```


Context Engine 不负责：

- 如何搜索
- 如何生成 Memory

只负责：

- 是否加入
- 加入多少
- 如何排序


---

# Retrieved Memory Handling


Retrieved Memory 不应该简单全部加入。


需要经过 Context Processing。


流程：

```
Retrieved Candidates

        |

        v

Filtering

        |

        v

Ranking

        |

        v

Token Budget Allocation

        |

        v

Context Inclusion

```


---

# Filtering


过滤低价值 Memory。


例如：

Memory Retrieval 返回：

```
100 chunks
```

Context Engine 不应该全部使用。


根据：

- score
- relevance
- token cost

过滤。


---

# Ranking


Context Engine 可以重新排序。


原因：

Memory Engine 的 ranking 目标：

```
找到相关记忆
```


Context Engine 的目标：

```
让 LLM 最容易理解
```


二者不同。


例如：

Memory Engine:

```
Chunk A score=0.95
Chunk B score=0.90
```


但是：

Chunk B 与当前 conversation 更连续。

Context Engine 可以调整顺序。


---

# Token Budget Management


Context Engine 必须管理 LLM Context 长度。


例如：

总预算：

```
8000 tokens
```


分配：

```
System Prompt

10%


Conversation Summary

20%


Recent Messages

30%


Retrieved Memories

40%

```


实际比例可以动态调整。


例如：

如果当前问题明显是记忆查询：

增加：

```
Retrieved Memories
```

如果当前问题是继续聊天：

增加：

```
Conversation History
```


---

# Conversation Memory Management


Conversation 本身需要长期保存。


建议：

```
Conversation

    |
    |
    +---- Messages

    |
    |
    +---- Summary

```


其中：


## Messages

保存原始聊天。


包含：

```
role

content

timestamp
```


例如：

```
user:

我第一次去东京是什么时候？


assistant:

你第一次去东京是在2019年。
```


---

## Summary

保存压缩后的长期状态。


用于：

- 长 conversation
- 重新打开历史 conversation
- 快速恢复上下文


---

# Retrieval Trace


每次 Memory Retrieval 可以记录：

```
Conversation Message

        |

        |

Retrieved Chunk IDs

        |

        |

Scores

```


用途：

- Debug
- 分析召回效果
- 优化 Retrieval Strategy


但是：

Retrieval Trace 不直接进入 LLM Context。


---

# Context Engine Responsibilities


Context Engine 负责：

```
✓ 管理 Prompt Context

✓ 压缩历史消息

✓ 管理 Token Budget

✓ 组合 Memory Retrieval Result

✓ 决定最终发送给 LLM 的内容

✓ 保存 Conversation State
```


---

# Context Engine Does NOT Handle


Context Engine 不负责：

```
✗ Memory Retrieval

✗ Embedding Search

✗ Tag Search

✗ Graph Search

✗ Memory Graph Construction

✗ Diary Chunk Storage
```


这些属于 Memory Engine。


---

# Future Extensions


未来可以增加：


## User Profile Context

例如：

```
用户喜欢摄影

用户常去日本旅行

用户有朋友A
```


作为长期 Context。


---

## Dynamic Context Planner

未来可以根据 Query 自动决定：

```
Conversation History 权重

Memory 权重

Summary 权重
```


例如：

查询：

```
我什么时候认识A？
```

增加：

```
Memory Context
```


查询：

```
继续刚才的话题
```

增加：

```
Conversation Context
```


---

# Final Architecture

整体系统：

```
                User

                 |

                 v

        Conversation Manager

                 |

                 v

          Context Engine

                 |

        +--------+---------+

        |                  |

        v                  v

Conversation State     Memory Engine

        |                  |

        |                  |

        +--------+---------+

                 |

                 v

          Final LLM Context

                 |

                 v

                LLM

```


核心思想：

Memory Engine 负责：

```
找到过去
```


Conversation Manager 负责：

```
保存现在
```


Context Engine 负责：

```
决定这一刻让模型看到什么
```

三者保持独立。