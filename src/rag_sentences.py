"""rag_sentences 表 CRUD（v0.4 检索基元）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.store import get_db, load_config


@dataclass
class RagSentenceRecord:
    id: str
    chunk_id: str
    text: str
    sent_index: int
    date: str
    source_file: str = ""
    model_version: str = "rag-sentence-v1"


def _cfg() -> dict[str, Any]:
    return load_config().get("paraphrase") or {}


def max_sentences_per_chunk() -> int:
    return int(_cfg().get("max_sentences_per_chunk", 30))


def model_version() -> str:
    return str(_cfg().get("model_version", "rag-sentence-v1"))


def delete_sentences_for_chunk(chunk_id: str) -> list[str]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id FROM rag_sentences WHERE chunk_id = ?", (chunk_id,)
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.execute("DELETE FROM rag_sentences WHERE chunk_id = ?", (chunk_id,))
            conn.commit()
        return ids
    finally:
        conn.close()


def save_sentences_for_chunk(
    chunk_id: str,
    sentences: list[str],
    *,
    date: str,
    source_file: str = "",
    model_ver: str | None = None,
) -> list[RagSentenceRecord]:
    delete_sentences_for_chunk(chunk_id)
    ver = model_ver or model_version()
    records: list[RagSentenceRecord] = []
    conn = get_db()
    try:
        for i, text in enumerate(sentences[: max_sentences_per_chunk()]):
            content = str(text or "").strip()
            if not content:
                continue
            sid = f"{chunk_id}_s{i}"
            rec = RagSentenceRecord(
                id=sid,
                chunk_id=chunk_id,
                text=content,
                sent_index=i,
                date=date,
                source_file=source_file,
                model_version=ver,
            )
            conn.execute(
                """INSERT INTO rag_sentences
                   (id, chunk_id, text, sent_index, date, source_file, model_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.id,
                    rec.chunk_id,
                    rec.text,
                    rec.sent_index,
                    rec.date,
                    rec.source_file,
                    rec.model_version,
                ),
            )
            records.append(rec)
        conn.commit()
    finally:
        conn.close()
    return records


def list_chunks_without_sentences(limit: int | None = None) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        sql = """
            SELECT c.id, c.date, c.text, c.source_file
            FROM chunks c
            LEFT JOIN rag_sentences s ON s.chunk_id = c.id
            WHERE s.id IS NULL
            ORDER BY c.date, c.id
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def list_all_chunks(limit: int | None = None) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT id, date, text, source_file FROM chunks ORDER BY date, id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def count_sentences() -> int:
    conn = get_db()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM rag_sentences").fetchone()[0])
    finally:
        conn.close()


def fetch_sentences(
    sentence_ids: list[str] | None = None,
) -> list[RagSentenceRecord]:
    conn = get_db()
    try:
        if sentence_ids:
            placeholders = ",".join("?" * len(sentence_ids))
            rows = conn.execute(
                f"""SELECT id, chunk_id, text, sent_index, date, source_file, model_version
                    FROM rag_sentences WHERE id IN ({placeholders})""",
                sentence_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, chunk_id, text, sent_index, date, source_file, model_version
                   FROM rag_sentences ORDER BY date, chunk_id, sent_index"""
            ).fetchall()
        return [
            RagSentenceRecord(
                id=r["id"],
                chunk_id=r["chunk_id"],
                text=r["text"],
                sent_index=int(r["sent_index"]),
                date=r["date"],
                source_file=r["source_file"] or "",
                model_version=r["model_version"] or "rag-sentence-v1",
            )
            for r in rows
        ]
    finally:
        conn.close()


def sentences_for_chunks(chunk_ids: list[str]) -> dict[str, list[RagSentenceRecord]]:
    """chunk_id → sentences 列表。"""
    if not chunk_ids:
        return {}
    conn = get_db()
    try:
        placeholders = ",".join("?" * len(chunk_ids))
        rows = conn.execute(
            f"""SELECT id, chunk_id, text, sent_index, date, source_file, model_version
                FROM rag_sentences WHERE chunk_id IN ({placeholders})
                ORDER BY chunk_id, sent_index""",
            chunk_ids,
        ).fetchall()
        out: dict[str, list[RagSentenceRecord]] = {cid: [] for cid in chunk_ids}
        for r in rows:
            rec = RagSentenceRecord(
                id=r["id"],
                chunk_id=r["chunk_id"],
                text=r["text"],
                sent_index=int(r["sent_index"]),
                date=r["date"],
                source_file=r["source_file"] or "",
                model_version=r["model_version"] or "rag-sentence-v1",
            )
            out.setdefault(rec.chunk_id, []).append(rec)
        return out
    finally:
        conn.close()
