## v0.4

.\.venv\Scripts\Activate.ps1

# 0. 环境（首次）
Copy-Item .env.example .env   # 填入 OPENROUTER_API_KEY
python scripts/check_env.py

# 1. 导入日记 → SQLite chunks
python main.py ingest

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

# 6. 启动问答
python main.py web
# 浏览器打开 http://127.0.0.1:8765

查看写好的rag-sentence
.\.venv\Scripts\python.exe -c "import json; from src.store import get_db; conn=get_db(); rows=conn.execute('SELECT id, chunk_id, date, sent_index, text, source_file FROM rag_sentences ORDER BY date, chunk_id, sent_index').fetchall(); conn.close(); out=[dict(r) for r in rows]; p='data/rag_sentences_export.json'; open(p,'w',encoding='utf-8').write(json.dumps(out,ensure_ascii=False,indent=2)); print(f'exported {len(out)} -> {p}')"

删除库
Remove-Item -Force -ErrorAction SilentlyContinue data\diary.db,data\lexicon.db,data\vocabulary.json,data\entities.json,data\chunk_entities.json,data\chunk_keywords.json,data\last_query.json,data\last_retrieval.json,data\last_context.json,data\rag_sentences_export.json; Remove-Item -Recurse -Force -ErrorAction SilentlyContinue data\chroma,data\retrieval_runs