"""先抽每个 chunk 的实体，再合并为全局 entities（支持抽样试跑）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.entities import build_and_save_entities, resolve_entity_sample_ratio


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 chunk_entity + 全局 entities")
    parser.add_argument(
        "--ratio",
        type=float,
        default=None,
        help="抽样比例 0~1（默认读 ENTITY_SAMPLE_RATIO / config，全量为 1）",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="跳过规则/LLM 实体清洗",
    )
    args = parser.parse_args()
    ratio = args.ratio if args.ratio is not None else resolve_entity_sample_ratio()

    print(f"entity 策略: sample_ratio={ratio}（先 chunk → 清洗 → 合并全局）")
    result, chunk_path, global_path = build_and_save_entities(
        sample_ratio=ratio,
        clean=False if args.no_clean else None,
    )
    by = result.global_entities.by_type or {}
    print(
        f"chunks: {result.n_chunks}（有实体: {result.n_with_entities}）→ {chunk_path.resolve()}"
    )
    print(
        f"全局实体: {len(result.global_entities.records)} "
        f"(person={by.get('person', 0)}, place={by.get('place', 0)}, org={by.get('org', 0)}) "
        f"→ {global_path.resolve()}"
    )
    print("DB: diary.db.chunk_entity + chunk_tags.entities；lexicon.db.entities")

    samples = [r for r in result.rows if r.entities][:3]
    for r in samples:
        ents = ", ".join(f"{e.name}/{e.entity_type}" for e in r.entities[:8])
        print(f"  [{r.date}] {r.chunk_id}: {ents}")


if __name__ == "__main__":
    main()
