"""按全局词表 V 为每个 chunk 提取 TF-IDF keywords，写入 DB + JSON。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.chunk_keywords import build_and_save_chunk_keywords, resolve_chunk_keyword_config


def main() -> None:
    cfg = resolve_chunk_keyword_config()
    print("chunk keywords 策略:")
    print(f"  keywords_per_chunk = {cfg.keywords_per_chunk}")
    print(f"  min_weight         = {cfg.min_weight}")
    print(f"  output             = {cfg.output_path}")
    print()

    result, path = build_and_save_chunk_keywords(cfg)
    print(f"chunks: {result.n_chunks}（有 tag: {result.n_tagged}）")
    print(f"词表 V: {result.vocab_size} 词，n_docs={result.n_docs}")
    print(f"JSON → {path.resolve()}")
    print("DB  → diary.db.chunk_tags.keywords + chunk_term")

    # 抽样展示
    samples = [r for r in result.rows if r.keywords][:5]
    if samples:
        print("\n样例:")
        for r in samples:
            terms = ", ".join(f"{w.term}({w.weight:.2f})" for w in r.keywords[:8])
            print(f"  [{r.date}] {r.chunk_id}")
            print(f"    {r.preview}")
            print(f"    tags: {terms}")


if __name__ == "__main__":
    main()
