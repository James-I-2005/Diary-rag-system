"""构建 rag-sentences：离线 paraphrase + 嵌入索引。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.paraphrase.pipeline import run_paraphrase_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="chunk → rag-sentence → Chroma")
    parser.add_argument("--ratio", type=float, default=None)
    parser.add_argument("--chunk-id", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sync-chroma-only", action="store_true")
    args = parser.parse_args()

    result = run_paraphrase_pipeline(
        ratio=args.ratio,
        chunk_id=args.chunk_id,
        force=args.force,
        limit=args.limit,
        sync_chroma_only=args.sync_chroma_only,
    )
    print(result)


if __name__ == "__main__":
    main()
