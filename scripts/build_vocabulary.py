"""从 chunks 表构建候选词表 V + 实体库（人名/地点/组织）。"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.entities import build_and_save_entities
from src.vocab_review import (
    rebuild_vocabulary_after_review,
    resolve_vocab_review_config,
    review_vocabulary,
)
from src.vocabulary import (
    build_vocabulary_from_db,
    resolve_vocabulary_config,
    save_vocabulary,
)


def main() -> None:
    cfg = resolve_vocabulary_config()
    review_cfg = resolve_vocab_review_config()

    print("词表策略:")
    print(f"  vocab_size         = {cfg.vocab_size}")
    print(f"  min_df_ratio       = {cfg.min_df_ratio}")
    print(f"  max_df_ratio       = {cfg.max_df_ratio}")
    print(f"  min_df_abs         = {cfg.min_df_abs}")
    print(f"  min_total_tf       = {cfg.min_total_tf}")
    print(f"  sort_by            = {cfg.sort_by}")
    print(f"  exclude_entity_terms = {cfg.exclude_entity_terms}")
    print(f"  review_enabled     = {review_cfg.enabled}")
    print(f"  output             = {cfg.output_path}")
    print()

    # 实体：先 chunk（出现即收录）→ 合并全局
    print("[entities] chunk → 合并全局...")
    entity_pack, chunk_ent_path, ent_path = build_and_save_entities()
    by = entity_pack.global_entities.by_type or {}
    print(
        f"  chunk 实体 → {chunk_ent_path.resolve()} "
        f"({entity_pack.n_chunks} chunks, {entity_pack.n_with_entities} 有实体)"
    )
    print(
        f"  全局实体 {len(entity_pack.global_entities.records)} "
        f"(person={by.get('person', 0)}, place={by.get('place', 0)}, org={by.get('org', 0)}) "
        f"→ {ent_path.resolve()}"
    )
    if entity_pack.global_entities.records:
        ge = entity_pack.global_entities.records
        print("  Top person:", [r.name for r in ge if r.entity_type == "person"][:8])
        print("  Top place:", [r.name for r in ge if r.entity_type == "place"][:8])

    result = build_vocabulary_from_db(cfg)
    print(f"[1/2] 初建关键词 V: {len(result.terms)} 词（候选 {result.n_candidates}）")

    review_result = None
    if review_cfg.enabled or review_cfg.apply_rule_filters:
        print("[review] 评估词表质量（规则 + LLM）...")
        review_result = review_vocabulary(result, review_cfg)
        if review_result.rule_rejected:
            print(f"  规则剔除: {len(review_result.rule_rejected)} 词")
        if review_result.llm_rejected:
            print(f"  LLM 剔除: {len(review_result.llm_rejected)} 词")
        if review_result.appended_stopwords:
            print(
                f"  写入停用词: +{len(review_result.appended_stopwords)} → "
                f"{', '.join(review_result.appended_stopwords[:12])}"
                f"{'...' if len(review_result.appended_stopwords) > 12 else ''}"
            )
        if review_cfg.rebuild_after_learn and review_result.appended_stopwords:
            print("[2/2] 停用词已更新，重新建表...")
            result = rebuild_vocabulary_after_review(cfg)
            print(f"  重建后: {len(result.terms)} 词")

    review_meta = asdict(review_result) if review_result else None
    out = save_vocabulary(result, review_meta=review_meta)

    print(f"\n文档数（chunks）: {result.n_docs}")
    print(f"写入词表 V: {len(result.terms)} 词 → {out.resolve()}")
    try:
        from src.lexicon_db import describe_lexicon_db

        info = describe_lexicon_db()
        print(
            f"lexicon DB ({info['backend']}): builds={info['vocab_builds']} "
            f"terms={info['vocab_terms']} stopwords={info['stopwords']} "
            f"entities={info.get('entities', 0)} {info.get('entities_by_type') or {}}"
        )
        if info.get("active_build"):
            print(f"  active build_id={info['active_build']['id']}")
    except Exception as exc:
        print(f"  [warn] lexicon DB 状态读取失败: {exc}")
    if result.records:
        print("\nTop 15 keywords:")
        for r in result.records[:15]:
            sc = r.score(result.n_docs, cfg.sort_by)
            print(f"  {r.term}\tdf={r.df}\ttf={r.total_tf}\tscore={sc:.2f}")


if __name__ == "__main__":
    main()
