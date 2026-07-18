"""日记导入：解析 → 切块 → 存库。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from src.store import (
    delete_chunks_by_source,
    get_db,
    load_config,
    resolve_diary_dir,
    save_chunks,
)


@dataclass
class DiaryEntry:
    date: str
    content: str
    source_file: str


@dataclass
class Chunk:
    id: str
    date: str
    text: str
    chunk_index: int
    source_file: str
    word_count: int


def parse_diary_file(filepath: str, date_pattern: str) -> list[DiaryEntry]:
    """按日期标题切分单文件中的多篇日记。"""
    text = Path(filepath).read_text(encoding="utf-8")
    pattern = re.compile(date_pattern, re.MULTILINE)
    matches = list(pattern.finditer(text))

    if not matches:
        return []

    entries: list[DiaryEntry] = []
    for i, match in enumerate(matches):
        date_str = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            entries.append(
                DiaryEntry(
                    date=date_str,
                    content=content,
                    source_file=Path(filepath).name,
                )
            )
    return entries


def chunk_text(
    text: str,
    max_chars: int = 500,
    overlap_chars: int = 50,
) -> list[str]:
    """将长文本切成带重叠的块。"""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]

        if end < len(text):
            last_period = chunk.rfind("。")
            if last_period > max_chars // 2:
                end = start + last_period + 1
                chunk = text[start:end]

        chunks.append(chunk.strip())
        start = max(end - overlap_chars, start + 1)

    return [c for c in chunks if c]


def entry_to_chunks(entry: DiaryEntry, max_chars: int, overlap: int) -> list[Chunk]:
    texts = chunk_text(entry.content, max_chars, overlap)
    return [
        Chunk(
            id=f"{entry.date}_{i}",
            date=entry.date,
            text=t,
            chunk_index=i,
            source_file=entry.source_file,
            word_count=len(t),
        )
        for i, t in enumerate(texts)
    ]


def ingest_all() -> int:
    cfg = load_config()
    diary_dir = resolve_diary_dir()
    diary_dir.mkdir(parents=True, exist_ok=True)
    max_chars = cfg["chunking"]["max_chars"]
    overlap = cfg["chunking"]["overlap_chars"]
    date_pattern = cfg["diary"]["date_pattern"]

    all_chunks: list[Chunk] = []
    for filepath in sorted(diary_dir.glob("*.md")):
        entries = parse_diary_file(str(filepath), date_pattern)
        for entry in entries:
            all_chunks.extend(entry_to_chunks(entry, max_chars, overlap))

    conn = get_db()
    save_chunks(all_chunks, conn)

    # 全量导入时同步 ingest_log，便于后续增量
    for filepath in sorted(diary_dir.glob("*.md")):
        entries = parse_diary_file(str(filepath), date_pattern)
        count = sum(
            len(entry_to_chunks(e, max_chars, overlap)) for e in entries
        )
        conn.execute(
            """INSERT OR REPLACE INTO ingest_log
               (source_file, file_hash, chunk_count)
               VALUES (?, ?, ?)""",
            (filepath.name, file_hash(filepath), count),
        )
    conn.commit()
    conn.close()
    return len(all_chunks)


def file_hash(filepath: Path) -> str:
    return hashlib.md5(filepath.read_bytes()).hexdigest()


def ingest_incremental() -> list[str]:
    """只处理新增或变更的日记文件，返回新增 chunk id 列表。"""
    cfg = load_config()
    diary_dir = resolve_diary_dir()
    diary_dir.mkdir(parents=True, exist_ok=True)
    max_chars = cfg["chunking"]["max_chars"]
    overlap = cfg["chunking"]["overlap_chars"]
    date_pattern = cfg["diary"]["date_pattern"]
    conn = get_db()

    new_chunk_ids: list[str] = []
    for filepath in sorted(diary_dir.glob("*.md")):
        fh = file_hash(filepath)
        row = conn.execute(
            "SELECT file_hash FROM ingest_log WHERE source_file = ?",
            (filepath.name,),
        ).fetchone()

        if row and row["file_hash"] == fh:
            continue

        delete_chunks_by_source(filepath.name, conn)

        entries = parse_diary_file(str(filepath), date_pattern)
        chunks: list[Chunk] = []
        for entry in entries:
            chunks.extend(entry_to_chunks(entry, max_chars, overlap))

        save_chunks(chunks, conn)
        new_ids = [c.id for c in chunks]
        new_chunk_ids.extend(new_ids)

        conn.execute(
            """INSERT OR REPLACE INTO ingest_log
               (source_file, file_hash, chunk_count)
               VALUES (?, ?, ?)""",
            (filepath.name, fh, len(chunks)),
        )

    conn.commit()
    conn.close()
    return new_chunk_ids


if __name__ == "__main__":
    count = ingest_all()
    print(f"导入完成，共 {count} 个 chunk")
