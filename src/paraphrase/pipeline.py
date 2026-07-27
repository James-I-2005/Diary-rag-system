"""批量 paraphrase + 写入 + 索引。"""

from __future__ import annotations

from typing import Any

from src.embed import delete_sentences_from_chroma, index_sentences
from src.paraphrase.agent import paraphrase_chunk
from src.rag_sentences import (
    count_sentences,
    delete_sentences_for_chunk,
    list_all_chunks,
    list_chunks_without_sentences,
    save_sentences_for_chunk,
)


def run_paraphrase_pipeline(
    *,
    ratio: float | None = None,
    chunk_id: str | None = None,
    force: bool = False,
    limit: int | None = None,
    sync_chroma_only: bool = False,
) -> dict[str, Any]:
    if sync_chroma_only:
        total = index_sentences()
        return {"chroma": total, "ok": 0, "fail": 0}

    import random

    if chunk_id:
        rows = [r for r in list_all_chunks() if r["id"] == chunk_id]
    elif force:
        rows = list_all_chunks(limit=limit)
    else:
        rows = list_chunks_without_sentences(limit=limit)

    if ratio is not None and 0 < ratio < 1 and rows:
        n = max(1, int(len(rows) * ratio))
        rows = random.sample(rows, min(n, len(rows)))

    if not rows:
        return {"ok": 0, "fail": 0, "message": "没有待处理 chunk"}

    ok = fail = 0
    deleted: list[str] = []
    for row in rows:
        cid = row["id"]
        try:
            result = paraphrase_chunk(cid, row["text"], date=row.get("date") or "")
            if not result.sentences:
                fail += 1
                print(f"  [skip] {cid}: 无 sentence")
                continue
            # 仅在新结果就绪后再覆盖；避免 force 时先删后失败导致句子丢失
            if force:
                deleted.extend(delete_sentences_for_chunk(cid))
            save_sentences_for_chunk(
                cid,
                result.sentences,
                date=row.get("date") or "",
                source_file=row.get("source_file") or "",
            )
            ok += 1
            print(f"  [ok] {cid}: {len(result.sentences)} sentences")
        except Exception as exc:
            fail += 1
            print(f"  [fail] {cid}: {exc}")

    if deleted:
        delete_sentences_from_chroma(deleted)
    chroma = index_sentences()
    return {
        "ok": ok,
        "fail": fail,
        "sqlite": count_sentences(),
        "chroma": chroma,
    }
