"""向量嵌入与检索（v0.4：主路径为 diary_sentences）。"""

from __future__ import annotations

import chromadb
from sentence_transformers import SentenceTransformer

from src.store import load_config, resolve_path

_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    global _model
    if _model is None:
        cfg = load_config()
        model_name = cfg["embedding"]["model"]
        print(f"加载嵌入模型: {model_name} ...")
        _model = SentenceTransformer(model_name)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_embedding_model()
    cfg = load_config()
    batch_size = cfg["embedding"]["batch_size"]
    embeddings = model.encode(texts, batch_size=batch_size, show_progress_bar=True)
    return embeddings.tolist()


def get_chroma_collection():
    """遗留 diary_chunks（只读兼容，主路径不再写入）。"""
    cfg = load_config()
    chroma_dir = str(resolve_path(cfg["data"]["chroma_dir"]))
    client = chromadb.PersistentClient(path=chroma_dir)
    return client.get_or_create_collection(
        name="diary_chunks",
        metadata={"hnsw:space": "cosine"},
    )


def get_sentences_collection():
    cfg = load_config()
    chroma_dir = str(resolve_path(cfg["data"]["chroma_dir"]))
    name = (cfg.get("paraphrase") or {}).get("chroma_collection", "diary_sentences")
    client = chromadb.PersistentClient(path=chroma_dir)
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def index_sentences(records: list | None = None) -> int:
    """将 rag_sentences 写入 Chroma diary_sentences。"""
    from src.rag_sentences import RagSentenceRecord, fetch_sentences

    if records is None:
        records = fetch_sentences()
    if not records:
        print("没有 rag-sentence 可索引")
        return 0

    rows: list[RagSentenceRecord] = []
    for r in records:
        if isinstance(r, RagSentenceRecord):
            rows.append(r)
        else:
            rows.append(
                RagSentenceRecord(
                    id=r["id"],
                    chunk_id=r["chunk_id"],
                    text=r["text"],
                    sent_index=int(r.get("sent_index", 0)),
                    date=r["date"],
                    source_file=r.get("source_file") or "",
                    model_version=r.get("model_version") or "rag-sentence-v1",
                )
            )

    ids = [r.id for r in rows]
    texts = [r.text for r in rows]
    metadatas = [
        {
            "chunk_id": r.chunk_id,
            "date": r.date,
            "source_file": r.source_file or "",
            "sent_index": r.sent_index,
        }
        for r in rows
    ]

    print(f"嵌入 {len(texts)} 个 rag-sentence ...")
    embeddings = embed_texts(texts)
    collection = get_sentences_collection()
    # chroma upsert 分批，避免过大
    batch = 500
    for i in range(0, len(ids), batch):
        sl = slice(i, i + batch)
        collection.upsert(
            ids=ids[sl],
            embeddings=embeddings[sl],
            documents=texts[sl],
            metadatas=metadatas[sl],
        )
    return collection.count()


def delete_sentences_from_chroma(sentence_ids: list[str]) -> None:
    if not sentence_ids:
        return
    collection = get_sentences_collection()
    if collection.count() == 0:
        return
    collection.delete(ids=sentence_ids)


def index_sentences_for_chunks(chunk_ids: list[str]) -> int:
    """索引指定 chunk 下的全部 sentences。"""
    from src.rag_sentences import sentences_for_chunks

    by_c = sentences_for_chunks(chunk_ids)
    records = [s for ss in by_c.values() for s in ss]
    if not records:
        print("指定 chunk 下没有 sentence 可索引")
        return get_sentences_collection().count()
    return index_sentences(records)


def index_all_chunks() -> None:
    """v0.4：主索引改为 sentences（命令名保留兼容）。"""
    total = index_sentences()
    print(f"sentence 索引完成，共 {total} 条")


def index_new_chunks(chunk_ids: list[str]) -> None:
    """v0.4：对新 chunk 对应的 sentences 建索引（需已 paraphrase）。"""
    if not chunk_ids:
        print("没有新内容需要嵌入")
        return
    total = index_sentences_for_chunks(chunk_ids)
    print(f"增量 sentence 索引完成，集合共 {total} 条")


def _parse_sentence_results(results: dict) -> list[dict]:
    hits: list[dict] = []
    if not results["ids"] or not results["ids"][0]:
        return hits
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i] or {}
        hits.append(
            {
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "date": meta.get("date", ""),
                "chunk_id": meta.get("chunk_id", ""),
                "distance": results["distances"][0][i],
                "score": 1 - results["distances"][0][i],
            }
        )
    return hits


def _chroma_date_where(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict | None:
    """构建 Chroma metadata 日期过滤；date 存 YYYY-MM-DD 时可字典序比较。"""
    start = (date_from or "").strip() or None
    end = (date_to or "").strip() or None
    if start and end and start > end:
        start, end = end, start
    if start and end:
        return {"$and": [{"date": {"$gte": start}}, {"date": {"$lte": end}}]}
    if start:
        return {"date": {"$gte": start}}
    if end:
        return {"date": {"$lte": end}}
    return None


def search_similar(
    query: str,
    top_k: int | None = None,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """向量检索 rag-sentences；可选按 date 闭区间过滤。"""
    cfg = load_config()
    k = top_k or cfg["retrieval"]["top_k"]

    collection = get_sentences_collection()
    if collection.count() == 0:
        return []

    query_embedding = embed_texts([query])[0]
    where = _chroma_date_where(date_from, date_to)
    kwargs: dict = {
        "query_embeddings": [query_embedding],
        "n_results": min(k, collection.count()),
        "include": ["documents", "metadatas", "distances"],
    }
    if where is not None:
        kwargs["where"] = where
    try:
        results = collection.query(**kwargs)
    except Exception as exc:
        # 无匹配 metadata / 旧索引缺 date 字段时降级为不带 where
        if where is not None:
            print(f"  [warn] Chroma 日期过滤失败，降级全库检索: {exc}")
            kwargs.pop("where", None)
            results = collection.query(**kwargs)
        else:
            raise
    return _parse_sentence_results(results)


def search_by_date_range(
    query: str, start_date: str, end_date: str, top_k: int = 20
) -> list[dict]:
    """兼容旧调用：等价于 search_similar(..., date_from=, date_to=)。"""
    return search_similar(
        query, top_k=top_k, date_from=start_date, date_to=end_date
    )


if __name__ == "__main__":
    index_all_chunks()
    results = search_similar("吃饭 美食")
    for r in results[:5]:
        print(f"[{r['date']}] score={r['score']:.3f} {r['text'][:50]}...")
