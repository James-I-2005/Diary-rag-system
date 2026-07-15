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

### Ollama（本地 LLM）

```powershell
ollama pull qwen2.5:7b
ollama list
ollama serve                          # 若未自动启动
ollama run qwen2.5:7b "你好"
```

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
