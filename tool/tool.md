## v0.4

.\.venv\Scripts\Activate.ps1

# 0. 环境（首次）
Copy-Item .env.example .env   # 填入 OPENROUTER_API_KEY
python scripts/check_env.py

# 1. 在 config.yaml 设置 extract.root 为日记原文目录（任意路径，不必在 data/ 下）
#    例如 root: "D:/Notes/my-diary"
# 2. 目录 Extract → Manifest → 导入 chunks
#    无 Agent：path → 正文 → mtime
python main.py extract
#    可选：path 未命中时用轻量路径 Agent（中文月等），再正文/mtime
# python main.py extract --agent
#    按 Manifest 建库（无 manifest 时会自动先 extract）
python main.py ingest
#    旧逻辑（仅顶层 *.md）：
# python main.py ingest --legacy
#    临时覆盖根目录：
# python main.py extract --root "D:/Notes/my-diary"
# 2. chunk → rag-sentence（调 LLM）并写入 SQLite + 索引 Chroma
python main.py sentences

# 3. 如需重建向量索引（sentences 已索引时可跳过）
python main.py index

# 4. 结构化标签（Tag 召回用，较慢）
python main.py tags

# 5.（推荐，Tag 通路更完整）词表 / 关键词 / 实体
python scripts/build_vocabulary.py
python scripts/build_chunk_keywords.py
python scripts/build_chunk_entities.py

# 6. 启动 / 重启问答 Web（先杀占用 8765 的进程再启动）
.\scripts\restart_web.ps1
# 或：python main.py web
# 浏览器打开 http://127.0.0.1:8765
# 侧栏「导入日记库」：填本机根目录 → extract → ingest →（可选）sentences
# Query Agent 默认 mode=react：先分析问题再调 grep(chunk原文)/rag_search
# 回退旧改写通路：config query_agent.mode: rewrite

查看写好的rag-sentence
.\.venv\Scripts\python.exe -c "import json; from src.store import get_db; conn=get_db(); rows=conn.execute('SELECT id, chunk_id, date, sent_index, text, source_file FROM rag_sentences ORDER BY date, chunk_id, sent_index').fetchall(); conn.close(); out=[dict(r) for r in rows]; p='data/rag_sentences_export.json'; open(p,'w',encoding='utf-8').write(json.dumps(out,ensure_ascii=False,indent=2)); print(f'exported {len(out)} -> {p}')"

删除库
Remove-Item -Force -ErrorAction SilentlyContinue data\diary.db,data\lexicon.db,data\vocabulary.json,data\entities.json,data\chunk_entities.json,data\chunk_keywords.json,data\last_query.json,data\last_retrieval.json,data\last_context.json,data\rag_sentences_export.json; Remove-Item -Recurse -Force -ErrorAction SilentlyContinue data\chroma,data\retrieval_runs