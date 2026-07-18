"""将 sinica_ith_diary_corpus/diary_corpus.csv 转为 My_rag 可 ingest 的 .md 文件。"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.store import load_config, resolve_diary_dir, resolve_path

WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日", "星期天")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _env_path(name: str, default: str) -> Path:
    raw = os.getenv(name, "").strip() or default
    return resolve_path(raw)


def _clean_content(content: str) -> str:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for day in WEEKDAYS:
        marker = day + "\n"
        idx = text.find(marker)
        if idx > 0:
            text = text[idx + len(marker) :]
            break
    return text.strip()


def _safe_filename(author: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", author.strip())
    return f"{name}.md"


def export_sinica(
    corpus_csv: Path,
    export_dir: Path,
    author: str | None = None,
) -> int:
    """按作者导出为「# YYYY-MM-DD」格式的 .md，返回写入篇数。"""
    if not corpus_csv.is_file():
        raise FileNotFoundError(f"找不到语料 CSV: {corpus_csv}")

    export_dir.mkdir(parents=True, exist_ok=True)
    by_author: dict[str, list[tuple[str, str]]] = defaultdict(list)

    with corpus_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_author = (row.get("author") or "").strip()
            if author and row_author != author:
                continue

            date_str = (row.get("time") or "").strip()
            if not DATE_RE.match(date_str):
                continue

            content = _clean_content(row.get("content") or "")
            if not content:
                continue

            by_author[row_author].append((date_str, content))

    if not by_author:
        raise ValueError(
            f"没有可导出的日记（author={author!r}）。请检查 SINICA_AUTHOR 或 CSV 路径。"
        )

    entry_count = 0
    for row_author, entries in sorted(by_author.items()):
        entries.sort(key=lambda x: x[0])
        out_path = export_dir / _safe_filename(row_author)
        parts: list[str] = []
        for date_str, content in entries:
            parts.append(f"# {date_str}\n\n{content}")
            entry_count += 1
        out_path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        print(f"  写入 {out_path.name}（{len(entries)} 篇）")

    return entry_count


def main() -> None:
    cfg = load_config()
    sinica_cfg = cfg.get("sinica") or {}

    parser = argparse.ArgumentParser(description="导入中研院日记 CSV → .md")
    parser.add_argument(
        "--csv",
        type=Path,
        default=_env_path(
            "SINICA_CORPUS_PATH",
            sinica_cfg.get("corpus_csv", "../sinica_ith_diary_corpus/diary_corpus.csv"),
        ),
        help="diary_corpus.csv 路径（可用环境变量 SINICA_CORPUS_PATH）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="导出目录（默认 DIARY_DIR 或 config data.diary_dir）",
    )
    parser.add_argument(
        "--author",
        default=os.getenv("SINICA_AUTHOR", sinica_cfg.get("author", "楊基振日記")),
        help="只导出指定作者；传空字符串 --author '' 导出全部 9 人",
    )
    args = parser.parse_args()

    export_dir = args.out or resolve_diary_dir()
    author = args.author.strip() or None

    print(f"CSV   : {args.csv.resolve()}")
    print(f"导出到: {export_dir.resolve()}")
    print(f"作者  : {author or '（全部）'}")

    count = export_sinica(args.csv, export_dir, author=author)
    print(f"完成，共 {count} 篇日记 → {export_dir}")


if __name__ == "__main__":
    main()
