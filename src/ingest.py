"""日记导入：Manifest / 解析 → 切块 → 存库。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from src.extract.dates import split_text_by_date_pattern
from src.extract.manifest import default_manifest_path, load_manifest
from src.store import (
    delete_chunks_by_source,
    get_db,
    load_config,
    resolve_diary_dir,
    resolve_path,
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
    """按日期标题切分单文件中的多篇日记（兼容旧调用）。"""
    path = Path(filepath)
    text = path.read_text(encoding="utf-8")
    return [
        DiaryEntry(date=seg.date, content=seg.content, source_file=path.name)
        for seg in split_text_by_date_pattern(text, date_pattern)
    ]


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


def _chunk_id(date: str, source_file: str, chunk_index: int) -> str:
    """date + source 短哈希 + index，避免同日多文件撞 id。"""
    h = hashlib.md5(source_file.encode("utf-8")).hexdigest()[:6]
    return f"{date}_{h}_{chunk_index}"


def entry_to_chunks(entry: DiaryEntry, max_chars: int, overlap: int) -> list[Chunk]:
    texts = chunk_text(entry.content, max_chars, overlap)
    return [
        Chunk(
            id=_chunk_id(entry.date, entry.source_file, i),
            date=entry.date,
            text=t,
            chunk_index=i,
            source_file=entry.source_file,
            word_count=len(t),
        )
        for i, t in enumerate(texts)
    ]


def ingest_from_manifest(manifest_path: str | Path | None = None) -> int:
    """根据 Extract Manifest 的 entries 切块入库。"""
    cfg = load_config()
    max_chars = cfg["chunking"]["max_chars"]
    overlap = cfg["chunking"]["overlap_chars"]

    path = Path(manifest_path) if manifest_path else default_manifest_path()
    if not path.is_absolute():
        path = resolve_path(str(path))
    if not path.is_file():
        raise FileNotFoundError(f"找不到 manifest: {path}（请先运行 extract）")

    manifest = load_manifest(path)
    conn = get_db()

    # 按 source path 覆盖旧 chunks
    sources = sorted({e.path for e in manifest.entries})
    for src in sources:
        delete_chunks_by_source(src, conn)
        # 兼容旧 ingest 只用文件名
        name = Path(src).name
        if name != src:
            delete_chunks_by_source(name, conn)

    all_chunks: list[Chunk] = []
    per_source_count: dict[str, int] = {s: 0 for s in sources}

    for entry in manifest.entries:
        if not (entry.text or "").strip():
            continue
        de = DiaryEntry(
            date=entry.date,
            content=entry.text,
            source_file=entry.path,
        )
        chunks = entry_to_chunks(de, max_chars, overlap)
        all_chunks.extend(chunks)
        per_source_count[entry.path] = per_source_count.get(entry.path, 0) + len(chunks)

    save_chunks(all_chunks, conn)

    root = resolve_path(manifest.root) if manifest.root else resolve_diary_dir()
    for src, count in per_source_count.items():
        abs_file = root / src if not Path(src).is_absolute() else Path(src)
        fh = file_hash(abs_file) if abs_file.is_file() else ""
        conn.execute(
            """INSERT OR REPLACE INTO ingest_log
               (source_file, file_hash, chunk_count)
               VALUES (?, ?, ?)""",
            (src, fh, count),
        )
    conn.commit()
    conn.close()
    return len(all_chunks)


def ingest_all(*, use_extract: bool = True, use_agent: bool = False) -> int:
    """
    全量导入。
    use_extract=True（默认）：extract → ingest_from_manifest。
    use_extract=False：旧逻辑，仅 diary_dir 顶层 *.md + 正文标题切分。
    """
    if use_extract:
        from src.extract.pipeline import run_extract_pipeline

        run_extract_pipeline(use_agent=use_agent)
        return ingest_from_manifest()

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
