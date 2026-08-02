"""预选池：rag / grep(+句重排) / tag(+句重排)；统一胜出句 metadata。"""

from __future__ import annotations

from typing import Any

import numpy as np

from src.embed import get_embedding_model
from src.engine.candidate import Candidate
from src.query import hydrate_candidates
from src.rag_sentences import sentences_for_chunks
from src.store import load_config
from src.tools import call_tool
from src.user_tags import list_chunks_for_tag


def _pool_cfg() -> dict[str, Any]:
    qa = load_config().get("query_agent") or {}
    return dict(qa.get("pool") or {})


def attach_winning_sentence(chunk: dict[str, Any]) -> dict[str, Any]:
    """从 matched_sentences[0] 写出 winning_* 字段。"""
    ms = list(chunk.get("matched_sentences") or [])
    ms_sorted = sorted(ms, key=lambda x: -float(x.get("score") or 0.0))
    if ms_sorted:
        chunk["matched_sentences"] = ms_sorted
        w = ms_sorted[0]
        chunk["winning_sentence_id"] = w.get("id") or ""
        chunk["winning_sentence_text"] = (w.get("text") or "").strip()
        chunk["winning_sentence_score"] = float(w.get("score") or 0.0)
    else:
        chunk.setdefault("winning_sentence_id", "")
        chunk.setdefault("winning_sentence_text", "")
        chunk.setdefault("winning_sentence_score", 0.0)
    return chunk


def _embed_matrix(texts: list[str]) -> np.ndarray:
    model = get_embedding_model()
    if not texts:
        return np.zeros((0, 1), dtype=np.float32)
    emb = model.encode(texts, batch_size=32, show_progress_bar=False)
    arr = np.asarray(emb, dtype=np.float32)
    # 余弦：归一化后点积
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
    return arr / norms


def rerank_chunk_ids_by_sentences(
    chunk_ids: list[str],
    query_texts: list[str],
    *,
    top_k: int,
    source: str,
) -> list[dict[str, Any]]:
    """
    在给定 chunk 集合内，用 rag-sentence 对 query_texts 做相似度；
    chunk 分 = 句分 max；再 hydrate 输出带 matched_sentences 的 chunk。
    """
    ids = [str(c).strip() for c in chunk_ids if str(c).strip()]
    queries = [str(q).strip() for q in query_texts if str(q).strip()]
    if not ids or not queries:
        return []

    by_c = sentences_for_chunks(ids)
    sents: list[Any] = []
    for cid in ids:
        sents.extend(by_c.get(cid) or [])
    if not sents:
        return []

    q_mat = _embed_matrix(queries)
    s_mat = _embed_matrix([s.text for s in sents])
    # 每个 sentence：与所有 query 的最大相似度
    # s_mat (n,d) @ q_mat.T (d,m) -> (n,m)
    sim = s_mat @ q_mat.T
    sent_scores = sim.max(axis=1)

    cands = [
        Candidate(
            unit_id=s.id,
            score=float(sent_scores[i]),
            source=source,
            meta={
                "parent_chunk_id": s.chunk_id,
                "sentence_text": s.text,
                "date": s.date,
            },
        )
        for i, s in enumerate(sents)
    ]
    chunks = hydrate_candidates(cands, top_k=max(1, int(top_k)))
    out: list[dict[str, Any]] = []
    for c in chunks:
        c["source"] = source
        attach_winning_sentence(c)
        out.append(c)
    return out


def pool_rag(
    *,
    themes: list[str],
    query: str,
    date_from: str | None,
    date_to: str | None,
    dates: list[str] | None,
    top_k: int | None = None,
    scheme: str | None = None,
) -> list[dict[str, Any]]:
    cfg = _pool_cfg()
    k = int(top_k if top_k is not None else cfg.get("rag_top_k", 20))
    out = call_tool(
        "rag_search",
        query=query or "",
        themes=themes or None,
        date_from=date_from,
        date_to=date_to,
        dates=dates,
        top_k=k,
        scheme=scheme,
    )
    chunks = list(out.get("chunks") or [])
    for c in chunks:
        c["source"] = "rag"
        attach_winning_sentence(c)
    return chunks


def pool_grep(
    *,
    terms: list[str],
    query_texts: list[str],
    date_from: str | None,
    date_to: str | None,
    dates: list[str] | None,
) -> list[dict[str, Any]]:
    cfg = _pool_cfg()
    match_cap = max(1, int(cfg.get("grep_match_cap", 150)))
    rerank_k = max(1, int(cfg.get("grep_rerank_top_k", 20)))
    if not terms:
        return []
    # grep 已 ORDER BY date DESC；top_k=match_cap 即按最近硬顶
    out = call_tool(
        "grep",
        terms=terms,
        date_from=date_from,
        date_to=date_to,
        dates=dates,
        top_k=match_cap,
    )
    raw = list(out.get("chunks") or [])
    chunk_ids = [str(c.get("chunk_id") or c.get("id") or "") for c in raw]
    chunk_ids = [c for c in chunk_ids if c][:match_cap]
    qtexts = query_texts or terms
    return rerank_chunk_ids_by_sentences(
        chunk_ids, qtexts, top_k=rerank_k, source="grep"
    )


def pool_tag(
    *,
    tag_ids: list[str],
    query_texts: list[str],
    date_from: str | None = None,
    date_to: str | None = None,
    dates: list[str] | None = None,
) -> list[dict[str, Any]]:
    """tag 绑定集 → 日期过滤 → 最近硬顶（多 tag 均分）→ 句级重排。"""
    cfg = _pool_cfg()
    match_cap = max(1, int(cfg.get("tag_match_cap", cfg.get("grep_match_cap", 150))))
    rerank_k = max(1, int(cfg.get("tag_rerank_top_k", cfg.get("grep_rerank_top_k", 20))))
    tids = [str(t).strip() for t in tag_ids if str(t).strip()]
    if not tids or not query_texts:
        return []

    per = max(1, match_cap // len(tids))
    dset = set(dates or [])
    start = (date_from or "").strip()
    end = (date_to or "").strip()
    if start and end and start > end:
        start, end = end, start

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tid in tids:
        n = 0
        for it in list_chunks_for_tag(tid, limit=match_cap):
            cid = str(it.get("chunk_id") or "")
            if not cid or cid in seen:
                continue
            d = str(it.get("date") or "")
            if dset and d not in dset:
                continue
            if start and d and d < start:
                continue
            if end and d and d > end:
                continue
            seen.add(cid)
            collected.append(it)
            n += 1
            if n >= per:
                break
        if len(collected) >= match_cap:
            break

    chunk_ids = [str(c["chunk_id"]) for c in collected][:match_cap]
    return rerank_chunk_ids_by_sentences(
        chunk_ids, query_texts, top_k=rerank_k, source="user_tag"
    )


def merge_pool_paths(*lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 chunk_id 去重；合并 sources；保留更高分与更完整胜出句。"""
    groups: dict[str, dict[str, Any]] = {}
    for lst in lists:
        for c in lst or []:
            cid = str(c.get("chunk_id") or c.get("id") or "").strip()
            if not cid:
                continue
            src = str(c.get("source") or "unknown")
            if cid not in groups:
                g = dict(c)
                g["id"] = cid
                g["chunk_id"] = cid
                g["sources"] = [src] if src else []
                groups[cid] = g
                continue
            g = groups[cid]
            if src and src not in g["sources"]:
                g["sources"].append(src)
            if float(c.get("score") or 0) > float(g.get("score") or 0):
                g["score"] = c.get("score")
                for key in (
                    "winning_sentence_id",
                    "winning_sentence_text",
                    "winning_sentence_score",
                    "matched_sentences",
                ):
                    if c.get(key) is not None:
                        g[key] = c.get(key)
            g["source"] = "+".join(g["sources"])
    return list(groups.values())


def build_subquestion_pool(
    *,
    sub_text: str,
    grep_terms: list[str],
    rag_themes: list[str],
    tag_ids: list[str],
    date_from: str | None,
    date_to: str | None,
    dates: list[str] | None,
    scheme: str | None = None,
) -> list[dict[str, Any]]:
    """单子问题三路预选并去重。"""
    themes = [t.strip() for t in rag_themes if str(t).strip()][:3]
    terms = [t.strip() for t in grep_terms if str(t).strip()]
    qtexts = themes or ([sub_text.strip()] if sub_text.strip() else terms)

    rag_chunks: list[dict[str, Any]] = []
    if themes or sub_text.strip():
        rag_chunks = pool_rag(
            themes=themes,
            query=sub_text or (themes[0] if themes else ""),
            date_from=date_from,
            date_to=date_to,
            dates=dates,
            scheme=scheme,
        )

    grep_chunks: list[dict[str, Any]] = []
    if terms:
        grep_chunks = pool_grep(
            terms=terms,
            query_texts=qtexts or terms,
            date_from=date_from,
            date_to=date_to,
            dates=dates,
        )

    tag_chunks: list[dict[str, Any]] = []
    if tag_ids and qtexts:
        tag_chunks = pool_tag(
            tag_ids=tag_ids,
            query_texts=qtexts,
            date_from=date_from,
            date_to=date_to,
            dates=dates,
        )

    return merge_pool_paths(rag_chunks, grep_chunks, tag_chunks)
