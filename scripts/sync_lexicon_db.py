"""把现有 vocabulary.json / stopwords_zh.txt 同步进 lexicon DB，或从 DB 导出。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.lexicon_db import (
    describe_lexicon_db,
    export_stopwords_db_to_file,
    resolve_lexicon_db_config,
    sync_entities_json_to_db,
    sync_stopwords_file_to_db,
    sync_vocabulary_json_to_db,
    upsert_entities,
    upsert_stopwords,
    upsert_vocab_terms,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="同步词表/停用词/实体 ↔ lexicon DB")
    parser.add_argument(
        "action",
        choices=[
            "status",
            "import",
            "export-stopwords",
            "upsert-stopword",
            "upsert-term",
            "upsert-entity",
        ],
        help="status | import | export-stopwords | upsert-stopword | upsert-term | upsert-entity",
    )
    parser.add_argument("--term", action="append", default=[], help="增补的词（可多次）")
    parser.add_argument(
        "--type",
        dest="entity_type",
        choices=["person", "place", "org"],
        default="person",
        help="实体类型（upsert-entity）",
    )
    parser.add_argument("--source", default="manual", help="来源标记")
    parser.add_argument("--comment", default="", help="备注")
    parser.add_argument("--df", type=int, default=0)
    parser.add_argument("--tf", type=int, default=0)
    parser.add_argument("--score", type=float, default=0.0)
    args = parser.parse_args()

    cfg = resolve_lexicon_db_config()
    print(f"backend={cfg.backend} sqlite={cfg.sqlite_path} url_set={bool(cfg.database_url)}")

    if args.action == "status":
        print(json.dumps(describe_lexicon_db(), ensure_ascii=False, indent=2))
        return

    if args.action == "import":
        n_sw = sync_stopwords_file_to_db(source="file_seed")
        build_id = sync_vocabulary_json_to_db(mark_active=True)
        n_ent = sync_entities_json_to_db(replace_extracted=True)
        print(f"导入停用词: {n_sw} 条")
        print(f"导入词表 build_id={build_id}")
        print(f"导入实体: {n_ent} 条")
        print(json.dumps(describe_lexicon_db(), ensure_ascii=False, indent=2))
        return

    if args.action == "export-stopwords":
        path = export_stopwords_db_to_file()
        print(f"已导出停用词 → {path.resolve()}")
        return

    if args.action == "upsert-stopword":
        if not args.term:
            raise SystemExit("请用 --term 指定至少一个停用词")
        written = upsert_stopwords(args.term, source=args.source, comment=args.comment)
        print(f"增补停用词 {len(written)}: {written}")
        return

    if args.action == "upsert-term":
        if not args.term:
            raise SystemExit("请用 --term 指定至少一个词表词")
        payload = [
            {"term": t, "df": args.df, "total_tf": args.tf, "score": args.score}
            for t in args.term
        ]
        n = upsert_vocab_terms(payload)
        print(f"增补/更新词表词 {n}: {[p['term'] for p in payload]}")
        return

    if args.action == "upsert-entity":
        if not args.term:
            raise SystemExit("请用 --term 指定至少一个实体名")
        payload = [
            {
                "name": t,
                "entity_type": args.entity_type,
                "df": args.df,
                "total_tf": args.tf or args.df,
            }
            for t in args.term
        ]
        n = upsert_entities(payload, source=args.source)
        print(f"增补实体 {n}: {[(p['name'], p['entity_type']) for p in payload]}")
        return


if __name__ == "__main__":
    main()
