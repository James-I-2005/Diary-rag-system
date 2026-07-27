"""RAG 工具：封装现有 embedding Scheme → hydrate 为 chunk。"""

from __future__ import annotations

from typing import Any

from src.engine import run_scheme
from src.query import hydrate_candidates, sentence_pool_size
from src.query_agent.models import StructuredQuery
from src.store import load_config
from src.tag_retrieve import resolve_retrieval_config


def _default_scheme() -> str:
    tools = (load_config().get("query_agent") or {}).get("tools") or {}
    rag = tools.get("rag_search") or {}
    return str(rag.get("default_scheme") or "embedding_only").strip() or "embedding_only"


def rag_search(
    *,
    query: str = "",
    themes: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_k: int | None = None,
    scheme: str | None = None,
    **_extra: Any,
) -> dict[str, Any]:
    """
    语义召回。themes 优先；否则用 query。
    日期通过 StructuredQuery 传给 EmbeddingOperator / Chroma where。
    """
    theme_list = [str(t).strip() for t in (themes or []) if str(t).strip()]
    q = (query or "").strip()
    if not theme_list and q:
        theme_list = [q]
    if not theme_list:
        return {
            "ok": True,
            "tool": "rag_search",
            "themes": [],
            "chunks": [],
            "count": 0,
        }

    cfg = resolve_retrieval_config()
    k = int(top_k) if top_k is not None else int(cfg.top_k)
    pool = sentence_pool_size(k)
    sch = (scheme or _default_scheme()).strip() or "embedding_only"

    structured = StructuredQuery(
        original_query=q or "；".join(theme_list),
        rewritten_query="；".join(theme_list),
        query_sentences=theme_list[:3],
        need_retrieval=True,
        retrieval_plan=["embedding"],
        embedding_query="\n".join(theme_list),
        source="tool",
        date_from=(date_from or "").strip(),
        date_to=(date_to or "").strip(),
    )
    search_q = structured.retrieval_query()
    candidates, used = run_scheme(
        search_q, sch, structured=structured, top_k=pool
    )
    chunks = hydrate_candidates(candidates, top_k=k)
    for c in chunks:
        c["source"] = c.get("source") or "rag"
        # 标记来自 rag 工具
        if c.get("source") == "embedding":
            c["source"] = "rag"

    return {
        "ok": True,
        "tool": "rag_search",
        "themes": theme_list,
        "scheme": used.to_public() if hasattr(used, "to_public") else {"id": sch},
        "chunks": chunks,
        "count": len(chunks),
    }
