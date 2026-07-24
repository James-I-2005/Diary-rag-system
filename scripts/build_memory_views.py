"""构建 Memory Views：离线提取 + 嵌入索引。"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.embed import delete_views_from_chroma, index_views
from src.memory_extraction import extract_views_for_chunk
from src.memory_views import (
    count_views,
    delete_views_for_chunk,
    list_all_chunks,
    list_chunks_without_views,
    save_views_for_chunk,
)


def _pick_chunks(
    *,
    ratio: float | None,
    chunk_id: str | None,
    force: bool,
    limit: int | None,
) -> list[dict]:
    if chunk_id:
        rows = [r for r in list_all_chunks() if r["id"] == chunk_id]
        return rows
    if force:
        rows = list_all_chunks(limit=limit)
    else:
        rows = list_chunks_without_views(limit=limit)
    if ratio is not None and 0 < ratio < 1 and rows:
        n = max(1, int(len(rows) * ratio))
        rows = random.sample(rows, min(n, len(rows)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="从 chunk 提取 Memory Views 并写入索引")
    parser.add_argument("--ratio", type=float, default=None, help="抽样比例 0–1")
    parser.add_argument("--chunk-id", default=None, help="只处理指定 chunk")
    parser.add_argument("--force", action="store_true", help="强制重跑（含已有 view 的 chunk）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理条数")
    parser.add_argument("--sync-chroma-only", action="store_true", help="仅重建 Chroma 索引")
    args = parser.parse_args()

    if args.sync_chroma_only:
        total = index_views()
        print(f"Chroma diary_views 共 {total} 条")
        return

    rows = _pick_chunks(
        ratio=args.ratio,
        chunk_id=args.chunk_id,
        force=args.force,
        limit=args.limit,
    )
    if not rows:
        print("没有待处理的 chunk")
        return

    print(f"处理 {len(rows)} 个 chunk ...")
    ok = fail = 0
    deleted_ids: list[str] = []

    for row in rows:
        cid = row["id"]
        try:
            if args.force:
                deleted_ids.extend(delete_views_for_chunk(cid))
            result = extract_views_for_chunk(
                cid, row["text"], date=row.get("date") or ""
            )
            if not result.views:
                print(f"  [skip] {cid}: 无有效 view")
                fail += 1
                continue
            save_views_for_chunk(
                cid,
                result.to_view_dicts(),
                date=row.get("date") or "",
                source_file=row.get("source_file") or "",
            )
            ok += 1
            print(f"  [ok] {cid}: {len(result.views)} views")
        except Exception as exc:
            fail += 1
            print(f"  [fail] {cid}: {exc}")

    if deleted_ids:
        delete_views_from_chroma(deleted_ids)

    total_chroma = index_views()
    print(f"完成：成功 {ok}，失败 {fail}；SQLite views={count_views()}，Chroma={total_chroma}")


if __name__ == "__main__":
    main()
