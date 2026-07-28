# Extract Pipeline · 目录提取 → Manifest → Ingest

> 状态：设计已定，待实现。  
> 原则：**Agent 只产出中间态（日期归属 + 待审名单）；写库只走确定性代码。**

---

## 1. 结论（是否赞同）

**赞同**，并固定为下面这条流水线。相对你口述的四点，只钉清三处，避免实现时分叉：

1. **Agent 输入是目录树（路径列表）**，不是整库正文；必要时可附「文件名前 N 字 / 正文前若干行」作弱线索，控制成本。
2. **Manifest 的原子单位是 Entry（一篇有日期的日记）**，不是 File。一个文件可对应 0～N 条 Entry（正文多日标题时常见）。
3. **Ingest 不再自己 `glob` + 猜日期**；只消费「已 resolved 的 Entry」，负责切块与落库。

日期级联（严格顺序）：

```text
1. 目录/文件名标准年月日正则（path）
2. Extract Agent（仅对 path 未解决；轻量、只看路径；需 --agent）
   → date | unknown（如 2026/八月/31）
3. 正文正则（content_regex）—— 仅 path+agent 仍未知时
4. mtime（confidence=low）
→ Manifest（维护已正确提取的 entries 集合）→ Ingest
```

---

## 2. 总流程

```text
指定 root（config `extract.root`，或 `--root` / `DIARY_DIR` / `data.diary_dir`）
        │
        ▼
┌───────────────────┐
│ 1. scan           │  os.walk → FileNode[]（相对路径、扩展名、mtime）
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ 2. path 正则      │  标准年月日；命中 → 加入已提取集合
└─────────┬─────────┘
          ▼（未解决）
┌───────────────────┐
│ 3. Extract Agent  │  轻量、只看路径（如 2026/八月/31）
│    （可选 LLM）   │  → date | unknown
└─────────┬─────────┘
          ▼（仍未知）
┌───────────────────┐
│ 4. 正文正则/mtime │  content_regex 切分；再不行整文件 mtime
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ 5. Manifest       │  已正确提取的 entries 集合
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ 6. Ingest         │  读 Manifest.entries → chunk → SQLite
└───────────────────┘
```

命令形态（实现时）：

```powershell
python main.py extract --root data/diary          # 1~4 → 写 manifest
python main.py ingest --from-manifest ...         # 5；可与 extract 串联
# 或
python main.py update --root data/diary           # extract → ingest → sentences → …
```

---

## 3. Manifest Schema

路径建议：`data/extract_manifest.json`（或 `data/manifests/<root_hash>.json`）。

```json
{
  "version": 1,
  "root": "data/diary",
  "created_at": "2026-07-26T20:00:00+08:00",
  "date_pattern": "#\\s*(\\d{4}-\\d{2}-\\d{2})",
  "files": [
    {
      "path": "2024/notes.md",
      "mtime": "2024-06-01T12:00:00",
      "status": "resolved"
    },
    {
      "path": "misc/scrap.txt",
      "mtime": "2023-01-02T08:00:00",
      "status": "resolved"
    }
  ],
  "entries": [
    {
      "id": "2024-03-15__2024_notes.md__0",
      "path": "2024/notes.md",
      "date": "2024-03-15",
      "date_source": "agent",
      "text": "……正文……",
      "confidence": "high"
    },
    {
      "id": "2024-03-16__2024_notes.md__1",
      "path": "2024/notes.md",
      "date": "2024-03-16",
      "date_source": "content_regex",
      "text": "……",
      "confidence": "high"
    },
    {
      "id": "2023-01-02__misc_scrap.txt__0",
      "path": "misc/scrap.txt",
      "date": "2023-01-02",
      "date_source": "mtime",
      "text": "……",
      "confidence": "low"
    }
  ],
  "agent_unresolved": [
    "misc/scrap.txt"
  ],
  "stats": {
    "files_total": 2,
    "entries_total": 3,
    "by_source": { "agent": 1, "content_regex": 1, "mtime": 1 }
  }
}
```

字段约定：

| 字段 | 含义 |
|------|------|
| `date_source` | `agent` \| `content_regex` \| `mtime` |
| `confidence` | `high` / `low`；`mtime` 固定 `low` |
| `agent_unresolved` | **仅 Agent 阶段结束后**仍无日期的路径；fallback 跑完后这些路径应已进 `entries`（除非读文件失败） |
| `text` | 已提取、可直接切块的正文（不含日期标题行，或与现 ingest 行为一致） |

**Agent 不写 `text` 进最终库的旁路**：实现上可让 Agent 只回 `{path, date?}`，由 fallback / 读盘步骤填 `text`，避免 LLM 改写原文。推荐：

- Agent 输出：`{ "resolved": [{"path","date","reason"}], "unresolved": ["path",…] }`
- 随后同一流水线读文件填 `text`（agent 命中且正文无多日标题 → 整文件一条；有多日标题 → 仍可用 regex 拆成多条，日期以正文为准或标 `conflict`）

冲突策略（实现时写死）：

```text
若 Agent 给了整文件 date，且正文 regex 又切出多日
  → 以 content_regex 多条为准（更细），date_source=content_regex
  → 可选：在 files[].note 记录 agent 曾给出的日期
```

---

## 4. 阶段职责（模块边界）

| 阶段 | 模块（建议） | LLM？ | 职责 |
|------|----------------|-------|------|
| scan | `src/extract/scan.py` | 否 | `os.walk`，过滤扩展名（默认 `.md`/`.txt`），产出 FileNode |
| agent | `src/extract/agent.py` + prompt | **是** | 只根据树（+弱线索）判日期；输出 resolved / unresolved |
| fallback | `src/extract/fallback.py` | 否 | 迁入/复用 `parse_diary_file`；mtime 兜底；组装 Entry.text |
| manifest | `src/extract/manifest.py` | 否 | 读写 JSON、校验、stats |
| ingest | `src/ingest.py`（改） | 否 | `ingest_from_manifest(path)`：Entry → Chunk → `save_chunks` |

**不注册 CLI filesystem tool。** Agent 若要「看文件头」，由流水线在调 LLM 前把弱线索拼进 prompt，或提供一个**代码侧** `peek(path, n_chars)`，仍不是开放 tool 循环。

---

## 5. Extract Agent 契约（唯一智能步）

**输入（代码组装）：**

```text
root = "data/diary"
tree_lines = [
  "2024/notes.md",
  "misc/scrap.txt",
  …
]
# 可选 peek：
peeks = { "misc/scrap.txt": "前 200 字…" }
```

**输出（严格 JSON）：**

```json
{
  "resolved": [
    { "path": "2024/notes.md", "date": "2024-03-15", "reason": "父目录 2024 + 文件名" }
  ],
  "unresolved": ["misc/scrap.txt"]
}
```

约束：

- `date` 必须 `YYYY-MM-DD`，否则视为 unresolved  
- 不得编造不存在的 path  
- 一个 path 在 Agent 阶段至多一个 date（多日留给 regex）  
- 宁可进 unresolved，不要瞎猜（mtime 比瞎猜可追溯）

---

## 6. Fallback（死代码）细节

对每个 `unresolved` path：

1. 读全文 → `parse_diary_file`（`config.diary.date_pattern`）  
2. 若 `entries` 非空 → 写入 Manifest，`date_source=content_regex`，并从「待处理」去掉  
3. 若为空 → `date = file.mtime.date()`（本地时区或配置时区），`text = 全文.strip()`，`date_source=mtime`，`confidence=low`  
4. 读失败 → `files[].status=error`，不进 entries，列入 `errors[]`

Agent 已 resolved 的 path：

1. 读全文  
2. 若正文 regex 能切出 ≥1 日 → **优先多条 regex**（见上冲突策略）  
3. 否则整文件一条，`date=agent.date`，`date_source=agent`

---

## 7. Ingest 调整点

现逻辑：`resolve_diary_dir()` → `glob("*.md")` → `parse_diary_file` → chunks。

改为：

```text
ingest_from_manifest(manifest_path):
  load entries
  for entry in entries:
    DiaryEntry(date, content=text, source_file=path)
    → entry_to_chunks → save_chunks
  # 按 source_file 做增量删除/覆盖策略与现 incremental 对齐
```

保留：

- `chunk_text` / `entry_to_chunks` / `save_chunks` 不动  
- `parse_diary_file` 迁到 `extract/fallback`（或 `extract/dates.py`），ingest 不再承担「发现日期」

兼容：无 manifest 时旧 `ingest_all()` 可暂时保留或标 deprecated。

---

## 8. 与 v0.4 写入主线的衔接

```text
extract（本设计）
  → Manifest
  → ingest_from_manifest
  → chunks
  → paraphrase → rag_sentences
  → embed → Chroma
  →（可选）tags
```

`02-写入流水线.md` 在实现后应在文首加一行：文档进入前可先 `extract`。

---

## 9. 实现顺序

| 步 | 内容 | 状态 |
|----|------|------|
| A | `FileNode` + `scan(root)` + Manifest 读写 | 已完成 |
| B | 迁出日期切分 → fallback；无 Agent 全走 regex→mtime | 已完成 |
| C | Extract Agent + prompt + JSON 校验 | 已完成（`--agent`） |
| D | 串起 A–C 写完整 Manifest | 已完成 `run_extract_pipeline` |
| E | `ingest_from_manifest` + `main.py extract/ingest` | 已完成 |
| F | `tool.md` / 本文件 | 已完成 |

```powershell
python main.py extract              # 无 Agent
python main.py extract --agent      # Agent + fallback
python main.py ingest               # 读 manifest；无则先 extract
python main.py ingest --legacy      # 旧顶层 *.md
```

---

## 10. 刻意不做

- 不把 extract 做成带 filesystem tool 的开放 Agent 循环  
- Agent 不直接写 SQLite / Chroma  
- 不在 Query / Context 运行时调用 extract  

---

## 11. 一句话

**扫盘 →（唯一）Agent 猜路径日期并列出失败 → 正则与 mtime 补全并抽正文 → Manifest → Ingest 只按 Manifest 切块入库。**
