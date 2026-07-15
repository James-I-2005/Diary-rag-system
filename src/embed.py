"""向量嵌入与检索。"""

from __future__ import annotations

import chromadb
from sentence_transformers import SentenceTransformer

from src.store import get_db, load_config, resolve_path

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
    cfg = load_config()
    chroma_dir = str(resolve_path(cfg["data"]["chroma_dir"]))
    client = chromadb.PersistentClient(path=chroma_dir)
    return client.get_or_create_collection(
        name="diary_chunks",
        metadata={"hnsw:space": "cosine"},
    )


def _upsert_rows(rows) -> int:
    if not rows:
        return 0

    ids = [r["id"] for r in rows]
    texts = [r["text"] for r in rows]
    metadatas = [
        {"date": r["date"], "source_file": r["source_file"] or ""} for r in rows
    ]

    print(f"嵌入 {len(texts)} 个 chunk ...")
    embeddings = embed_texts(texts)

    collection = get_chroma_collection()
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    return collection.count()


def index_all_chunks() -> None:
    """把 SQLite 中所有 chunk 嵌入并写入 Chroma。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, date, text, source_file FROM chunks"
    ).fetchall()
    conn.close()

    if not rows:
        print("没有 chunk 可索引")
        return

    total = _upsert_rows(rows)
    print(f"索引完成，共 {total} 条")


def index_new_chunks(chunk_ids: list[str]) -> None:
    """只嵌入并写入指定 id 的 chunk。"""
    if not chunk_ids:
        print("没有新 chunk 需要嵌入")
        return

    conn = get_db()
    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"SELECT id, date, text, source_file FROM chunks WHERE id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    conn.close()

    total = _upsert_rows(rows)
    print(f"增量索引完成，本次 {len(rows)} 条，集合共 {total} 条")


def _parse_query_results(results: dict) -> list[dict]:
    chunks = []
    if not results["ids"] or not results["ids"][0]:
        return chunks
    for i in range(len(results["ids"][0])):
        chunks.append(
            {
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "date": results["metadatas"][0][i]["date"],
                "distance": results["distances"][0][i],
                "score": 1 - results["distances"][0][i],
            }
        )
    return chunks


def search_similar(query: str, top_k: int | None = None) -> list[dict]:
    """向量检索，返回相关 chunk 列表。"""
    cfg = load_config()
    k = top_k or cfg["retrieval"]["top_k"]

    query_embedding = embed_texts([query])[0]
    collection = get_chroma_collection()
    if collection.count() == 0:
        return []

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    return _parse_query_results(results)


def search_by_date_range(
    query: str, start_date: str, end_date: str, top_k: int = 20
) -> list[dict]:
    query_embedding = embed_texts([query])[0]
    collection = get_chroma_collection()
    if collection.count() == 0:
        return []

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
        where={
            "$and": [
                {"date": {"$gte": start_date}},
                {"date": {"$lte": end_date}},
            ]
        },
        include=["documents", "metadatas", "distances"],
    )
    return _parse_query_results(results)


if __name__ == "__main__":
    index_all_chunks()
    results = search_similar("吃饭 美食")
    for r in results[:5]:
        print(f"[{r['date']}] score={r['score']:.3f} {r['text'][:50]}...")
