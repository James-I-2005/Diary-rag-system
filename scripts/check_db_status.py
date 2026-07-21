"""打印 diary.db / Chroma 覆盖率（楊基振建库后自检）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.embed import get_chroma_collection
from src.store import get_db, resolve_diary_dir


def main() -> None:
    print("DIARY_DIR =", resolve_diary_dir())
    conn = get_db()
    try:
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        n_tags = conn.execute("SELECT COUNT(*) FROM chunk_tags").fetchone()[0]
        n_ent = conn.execute(
            "SELECT COUNT(DISTINCT chunk_id) FROM chunk_entity"
        ).fetchone()[0]
        n_term = conn.execute(
            "SELECT COUNT(DISTINCT chunk_id) FROM chunk_term"
        ).fetchone()[0]
        d0, d1 = conn.execute(
            "SELECT MIN(date), MAX(date) FROM chunks"
        ).fetchone()
        print(f"chunks={n_chunks}  date={d0}..{d1}")
        for r in conn.execute(
            "SELECT source_file, COUNT(*) AS n FROM chunks GROUP BY source_file"
        ):
            print(f"  {r['source_file']}: {r['n']}")
        print(f"chunk_tags={n_tags}  term_chunks={n_term}  entity_chunks={n_ent}")
    finally:
        conn.close()
    print(f"chroma={get_chroma_collection().count()}")


if __name__ == "__main__":
    main()
