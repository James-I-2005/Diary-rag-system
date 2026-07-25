# My_rag 常用命令速查

以下命令均在 **`My_rag/`** 目录下执行（含 `main.py`、`config.yaml` 的那一层）。

---

## 1. 环境

```powershell
# 创建 / 激活虚拟环境（Windows）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 若 Activate 被策略拦截
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser

# 安装依赖
pip install -r requirements.txt

# 检查 Python / 依赖 / 配置
python --version
python scripts/check_env.py
```

### 中研院日记语料（sinica_ith_diary_corpus）

```powershell
# .env 示例：
# DIARY_DIR=data/diary_sinica
# SINICA_CORPUS_PATH=../sinica_ith_diary_corpus/diary_corpus.csv
# SINICA_AUTHOR=楊基振日記

python scripts/import_sinica.py              # CSV → .md（写入 DIARY_DIR）
python scripts/import_sinica.py --author ""  # 导出全部 9 位作者（体积大）
python main.py ingest
python main.py index
python main.py tags
```

### OpenRouter（标签 / 问答）

```powershell
Copy-Item .env.example .env
# 编辑 .env 填入 OPENROUTER_API_KEY=sk-or-v1-...
python scripts/check_env.py
python scripts/demo_tokenize.py
python scripts/demo_tokenize.py "我和 Jenny 约会那天的回忆"
python -m src.tokenize

# v0.1 候选词表（需先 ingest；含 LLM 自学习停用词；同步写 JSON + lexicon.db）
python scripts/build_vocabulary.py
# 调参：.env 设 VOCAB_MAX_DF_RATIO=0.45；关闭 LLM 评估设 VOCAB_REVIEW_ENABLED=false

# 按全局 V 为每个 chunk 打 TF-IDF keywords（DB + JSON）
python scripts/build_chunk_keywords.py
# KEYWORDS_PER_CHUNK=15 可调 Top-K

# chunk 实体（出现即收录）→ 规则+LLM 清洗 → 合并全局；--ratio 0.05 抽样试跑
python scripts/build_chunk_entities.py --ratio 0.05
python scripts/build_chunk_entities.py

# ★ 一键重建楊基振全文离线缓存（正文 + Chroma + 实体 + 词表 + keywords）
# 推荐首次：跳过实体/词表 LLM，规则清洗即可（快且稳）
python scripts/build_offline_cache.py --no-entity-llm --no-vocab-llm
# 已有 md / 已 ingest+embed 时续跑：
# python scripts/build_offline_cache.py --skip-import --skip-ingest --skip-embed --no-entity-llm --no-vocab-llm
# 覆盖率检查：
python scripts/check_db_status.py
# tag 召回 / Engine Plan 试跑（不走向量）
python -m src.query
python -m src.engine
# 切换 Plan / 方案：.env 设 RETRIEVAL_SCHEME=weighted_50_50（默认）
#   或 union_max / tag_only / embedding_only
# Web 顶栏可切换检索方案
# 召回结果 JSON：data/last_retrieval.json（历史在 data/retrieval_runs/）

# v0.4 rag-sentence（取代 chunk/view 作为检索基元）
python main.py sentences                 # 全量：无 sentence 的 chunk → paraphrase → 索引
python scripts/build_rag_sentences.py --ratio 0.05
python scripts/build_rag_sentences.py --sync-chroma-only
python main.py index                     # 重建 diary_sentences 索引
# 召回 id 形如 {chunk_id}_s0

# Context Engine 多轮（记忆临时进 Prompt，不进聊天历史）
python -m src.context
python main.py chat
# Query Agent 调试（改写 + query rag-sentences）
python -m src.query_agent
# 输出见 data/last_query.json（rewritten_query / query_sentences）
# 聊天内命令：/new /list /use <id前缀> /title <名称>
# Web 界面（ChatGPT 风格）
python main.py web
# 浏览器打开 http://127.0.0.1:8765
# 调试：data/last_context.json

# 词表/停用词库：导入现有 JSON/txt、状态、PG 增补
python scripts/sync_lexicon_db.py status
python scripts/sync_lexicon_db.py import
python scripts/sync_lexicon_db.py upsert-stopword --term 可以 --term 已經 --source manual
python scripts/sync_lexicon_db.py upsert-term --term 中秋 --df 10 --tf 12 --score 8.5
python scripts/sync_lexicon_db.py upsert-entity --type person --term 楊基振 --df 5
# 切 PostgreSQL：.env 设 LEXICON_DB_BACKEND=postgres 与 LEXICON_DATABASE_URL=postgresql://...

```

嵌入仍用本地 `sentence-transformers`；Ollama 已非默认依赖。

---

## 2. 正式流水线（`main.py`）

首次全量：

```powershell
python main.py ingest                 # 全量导入日记 → SQLite
python main.py index                  # 全量嵌入 → Chroma
python main.py tags                   # 全量标签提取（较慢）
```

日常：

```powershell
python main.py update                 # 增量：导入 → 嵌入 → 标签
```

提问 / 测试：

```powershell
python main.py                        # 交互聊天（空行退出）
python main.py chat                   # 同上
python main.py "这周吃了什么"           # 单次提问
python main.py 吃了几次火锅             # 多词问题也可（无需引号，视 shell 而定）
python main.py test                   # 跑 tests/test_queries.yaml
```

建议提问样例：

```powershell
python main.py "这个月所有关于吃饭的内容"    # 检索
python main.py "感动瞬间有多少次"           # 统计
python main.py "这个月最喜欢做的事情是什么"   # 归纳
```

---

## 3. 模块级调试（跳过 CLI）

单模块 `__main__`，便于单独验证某一层：

```powershell
python -m src.ingest                  # 全量导入，打印 chunk 数
python -m src.embed                   # 全量索引 + 试搜「吃饭 美食」
python -m src.extract_tags            # 对尚未打标签的 chunk 提取
python -m src.query                   # 跑内置三类问题样例（只检索/统计，不生成回答）
python tests/run_tests.py             # 等价于 python main.py test
```

---

## 4. 查库 / 查向量（调试数据）

```powershell
# SQLite：看表与行数
python -c "from src.store import get_db; c=get_db(); print([r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]); print('chunks', c.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]); print('tags', c.execute('SELECT COUNT(*) FROM chunk_tags').fetchone()[0]); print('ingest_log', list(c.execute('SELECT source_file, chunk_count FROM ingest_log'))); c.close()"

# 看几条 chunk
python -c "from src.store import get_db; c=get_db(); [print(dict(r)) for r in c.execute('SELECT id, date, substr(text,1,40) AS t FROM chunks LIMIT 5')]; c.close()"

# 看标签
python -c "from src.store import get_db; c=get_db(); [print(dict(r)) for r in c.execute('SELECT chunk_id, food_mentions, is_touching_moment FROM chunk_tags LIMIT 5')]; c.close()"

# Chroma 条数
python -c "from src.embed import get_chroma_collection; print(get_chroma_collection().count())"
```

若已安装 `sqlite-utils`：

```powershell
sqlite-utils tables data/diary.db
sqlite-utils rows data/diary.db chunks --limit 5
sqlite-utils rows data/diary.db chunk_tags --limit 5
sqlite-utils rows data/diary.db ingest_log
```

---

## 5. 备份（数据 / 向量）

```powershell
New-Item -ItemType Directory -Force -Path backup | Out-Null
$date = Get-Date -Format "yyyy-MM-dd"
Compress-Archive -Path data/diary.db, data/chroma -DestinationPath "backup/diary_$date.zip" -Force
```

恢复：解压后覆盖回 `data/` 对应路径即可。

---

## 6. 推荐操作顺序（备忘）

| 场景 | 命令 |
|------|------|
| 刚配好环境 | `python scripts/check_env.py` → `ollama list` |
| 第一次跑通 | `ingest` → `index` → `tags` → 提问 / `test` |
| 加了新日记 | `python main.py update` |
| 改路由/查询逻辑 | `python -m src.query` 或 `python main.py test` |
| 改回答 prompt | `python main.py "…"` |
| 怀疑库空了 | 第 4 节查库命令 |

数据路径见 `config.yaml`：`data/diary/`、`data/diary.db`、`data/chroma/`。
