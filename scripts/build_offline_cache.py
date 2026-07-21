"""一键重建楊基振（或当前 DIARY_DIR）离线缓存：正文 → 向量 → 词表/关键词/实体。

用法（在 My_rag/ 下）:
  python scripts/build_offline_cache.py
  python scripts/build_offline_cache.py --skip-import --skip-embed
  python scripts/build_offline_cache.py --no-entity-llm
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

PY = sys.executable


def run(cmd: list[str]) -> None:
    print("\n$", " ".join(cmd), flush=True)
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    subprocess.run(cmd, cwd=str(ROOT), check=True, env=env)


def _clear_content_tables() -> None:
    from src.store import get_db

    conn = get_db()
    try:
        for table in (
            "chunk_entity",
            "chunk_term",
            "chunk_tags",
            "chunks",
            "ingest_log",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        print("已清空 chunks / tags / term / entity / ingest_log")
    finally:
        conn.close()


def _clear_chroma() -> None:
    from src.embed import get_chroma_collection

    col = get_chroma_collection()
    ids = col.get(include=[])["ids"]
    if not ids:
        print("Chroma 本为空")
        return
    batch = 500
    for i in range(0, len(ids), batch):
        col.delete(ids=ids[i : i + batch])
    print(f"已清空 Chroma {len(ids)} 条")


def step_import(author: str) -> None:
    print(f"\n=== 1) import_sinica (author={author!r}) ===")
    run([PY, "scripts/import_sinica.py", "--author", author, "--out", "data/diary_sinica"])


def step_ingest() -> None:
    print("\n=== 2) ingest 全文 → diary.db ===")
    os.environ["DIARY_DIR"] = "data/diary_sinica"
    _clear_content_tables()
    from src.ingest import ingest_all

    n = ingest_all()
    print(f"导入 {n} chunks")


def step_embed() -> None:
    print("\n=== 3) embedding → Chroma ===")
    _clear_chroma()
    from src.embed import index_all_chunks

    index_all_chunks()


def step_vocabulary(*, with_entities: bool) -> None:
    print("\n=== vocabulary ===")
    if with_entities:
        run([PY, "scripts/build_vocabulary.py"])
    else:
        run([PY, "scripts/build_vocabulary_only.py"])


def step_keywords() -> None:
    print("\n=== chunk keywords ===")
    run([PY, "scripts/build_chunk_keywords.py"])


def step_entities(*, no_llm: bool) -> None:
    print("\n=== chunk entities（全量）===")
    os.environ["ENTITY_SAMPLE_RATIO"] = "1.0"
    if no_llm:
        # 保留规则清洗，跳过 LLM（避免 API 卡住 / 费时）
        os.environ["ENTITY_REVIEW_ENABLED"] = "false"
    cmd = [PY, "scripts/build_chunk_entities.py", "--ratio", "1.0"]
    run(cmd)


def step_report() -> None:
    from src.embed import get_chroma_collection
    from src.store import get_db

    print("\n=== 完成：覆盖率 ===")
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
        sources = list(
            conn.execute(
                "SELECT source_file, COUNT(*) AS n FROM chunks GROUP BY source_file"
            )
        )
        print(f"chunks={n_chunks}  date={d0}..{d1}")
        for r in sources:
            print(f"  source={r['source_file']} n={r['n']}")
        print(f"chunk_tags={n_tags}  term_chunks={n_term}  entity_chunks={n_ent}")
    finally:
        conn.close()
    print(f"chroma={get_chroma_collection().count()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="重建离线日记 + tag 缓存")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--skip-embed", action="store_true")
    parser.add_argument("--skip-vocab", action="store_true")
    parser.add_argument("--skip-keywords", action="store_true")
    parser.add_argument("--skip-entities", action="store_true")
    parser.add_argument(
        "--no-entity-llm",
        action="store_true",
        help="实体只做规则清洗，跳过 LLM（推荐全量首次建库）",
    )
    parser.add_argument(
        "--no-vocab-llm",
        action="store_true",
        help="词表跳过 LLM 停用词学习",
    )
    parser.add_argument(
        "--vocab-includes-entities",
        action="store_true",
        help="用 build_vocabulary.py 顺带建实体（会与 --skip-entities 二选一）",
    )
    parser.add_argument(
        "--author",
        default=os.getenv("SINICA_AUTHOR", "楊基振日記"),
    )
    args = parser.parse_args()

    # 强制本流程写到 diary_sinica；全量实体
    os.environ["DIARY_DIR"] = "data/diary_sinica"
    os.environ["ENTITY_SAMPLE_RATIO"] = "1.0"
    os.environ["PYTHONUNBUFFERED"] = "1"
    if args.no_entity_llm:
        os.environ["ENTITY_REVIEW_ENABLED"] = "false"
    if args.no_vocab_llm:
        os.environ["VOCAB_REVIEW_ENABLED"] = "false"

    if not args.skip_import:
        step_import(args.author.strip())
    if not args.skip_ingest:
        step_ingest()
    if not args.skip_embed:
        step_embed()

    # 默认：先独立建实体，再只建词表（避免 build_vocabulary 内重复抽实体）
    if args.vocab_includes_entities:
        if not args.skip_vocab:
            step_vocabulary(with_entities=True)
        if not args.skip_keywords:
            step_keywords()
    else:
        if not args.skip_entities:
            step_entities(no_llm=args.no_entity_llm)
        if not args.skip_vocab:
            step_vocabulary(with_entities=False)
        if not args.skip_keywords:
            step_keywords()

    step_report()


if __name__ == "__main__":
    main()
