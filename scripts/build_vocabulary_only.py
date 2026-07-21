"""仅构建词表 V（不抽实体）。供 build_offline_cache 调用。"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

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
    print(f"vocab_size={cfg.vocab_size} review_enabled={review_cfg.enabled}")

    result = build_vocabulary_from_db(cfg)
    print(f"[1/2] 初建关键词 V: {len(result.terms)} 词（候选 {result.n_candidates}）")

    review_result = None
    if review_cfg.enabled or review_cfg.apply_rule_filters:
        print("[review] 评估词表…")
        review_result = review_vocabulary(result, review_cfg)
        if review_result.rule_rejected:
            print(f"  规则剔除: {len(review_result.rule_rejected)}")
        if review_result.llm_rejected:
            print(f"  LLM 剔除: {len(review_result.llm_rejected)}")
        if review_cfg.rebuild_after_learn and review_result.appended_stopwords:
            print("[2/2] 停用词已更新，重新建表…")
            result = rebuild_vocabulary_after_review(cfg)
            print(f"  重建后: {len(result.terms)} 词")

    out = save_vocabulary(
        result, review_meta=asdict(review_result) if review_result else None
    )
    print(f"文档数: {result.n_docs}")
    print(f"写入词表 V: {len(result.terms)} 词 → {out.resolve()}")


if __name__ == "__main__":
    main()
