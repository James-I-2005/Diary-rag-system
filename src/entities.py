"""语料实体：先抽每个 chunk（出现即收录），再合并为全局 entities。"""

from __future__ import annotations

import json
import os
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.store import get_db, load_config, resolve_path
from src.tokenize import extract_entities

EntityType = Literal["person", "place", "org"]

_TYPE_MAP: dict[str, EntityType] = {
    "people": "person",
    "places": "place",
    "orgs": "org",
}


@dataclass
class EntityRecord:
    name: str
    entity_type: EntityType
    df: int
    total_tf: int


@dataclass
class ChunkEntityItem:
    name: str
    entity_type: EntityType
    tf: int = 1


@dataclass
class ChunkEntityRow:
    chunk_id: str
    date: str
    entities: list[ChunkEntityItem] = field(default_factory=list)
    preview: str = ""


@dataclass
class EntityBuildResult:
    """全局实体（由 chunk 合并）。"""

    records: list[EntityRecord]
    n_docs: int = 0
    by_type: dict[str, int] | None = None


@dataclass
class ChunkEntityBuildResult:
    rows: list[ChunkEntityRow]
    global_entities: EntityBuildResult
    n_chunks: int = 0
    n_with_entities: int = 0
    sample_ratio: float = 1.0
    review_meta: dict[str, Any] | None = None


def resolve_entities_output_path() -> str:
    cfg = load_config().get("vocabulary") or {}
    return (
        os.getenv("ENTITIES_OUTPUT_PATH", "").strip()
        or cfg.get("entities_output_path", "data/entities.json")
    )


def resolve_chunk_entities_output_path() -> str:
    cfg = load_config().get("vocabulary") or {}
    return (
        os.getenv("CHUNK_ENTITIES_OUTPUT", "").strip()
        or cfg.get("chunk_entities_output", "data/chunk_entities.json")
    )


def resolve_entity_sample_ratio() -> float:
    cfg = load_config().get("vocabulary") or {}
    raw = os.getenv("ENTITY_SAMPLE_RATIO", "").strip()
    if raw:
        return max(0.0, min(1.0, float(raw)))
    return max(0.0, min(1.0, float(cfg.get("entity_sample_ratio") or 1.0)))


def extract_entities_for_text(text: str) -> list[ChunkEntityItem]:
    """单段文本：与 question 侧同一套 extract_entities；出现即收录。"""
    if not text or not text.strip():
        return []
    extracted = extract_entities(text)
    items: list[ChunkEntityItem] = []
    seen: set[tuple[str, EntityType]] = set()
    for key, etype in _TYPE_MAP.items():
        for name in extracted.get(key) or []:
            name = name.strip()
            if not name:
                continue
            pair = (name, etype)
            if pair in seen:
                continue
            seen.add(pair)
            tf = max(text.count(name), 1)
            items.append(ChunkEntityItem(name=name, entity_type=etype, tf=tf))
    items.sort(key=lambda x: (x.entity_type, -x.tf, x.name))
    return items


def merge_chunk_entities_to_global(
    rows: list[ChunkEntityRow],
) -> EntityBuildResult:
    """全局实体 = 各 chunk 实体并集 + df/total_tf 聚合。"""
    df: Counter[tuple[str, EntityType]] = Counter()
    tf: Counter[tuple[str, EntityType]] = Counter()
    n_docs = 0
    for row in rows:
        if not row.chunk_id:
            continue
        n_docs += 1
        for item in row.entities:
            pair = (item.name, item.entity_type)
            df[pair] += 1
            tf[pair] += max(item.tf, 1)

    records = [
        EntityRecord(
            name=name,
            entity_type=etype,
            df=df[(name, etype)],
            total_tf=tf[(name, etype)],
        )
        for (name, etype) in df.keys()
    ]
    records.sort(key=lambda r: (-r.df, -r.total_tf, r.entity_type, r.name))
    by_type: dict[str, int] = defaultdict(int)
    for r in records:
        by_type[r.entity_type] += 1
    return EntityBuildResult(records=records, n_docs=n_docs, by_type=dict(by_type))


def _select_chunk_rows(
    *,
    sample_ratio: float,
    seed: int = 42,
) -> list[Any]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, date, text FROM chunks ORDER BY date, id"
    ).fetchall()
    conn.close()
    if not rows:
        raise ValueError("chunks 表为空，请先运行 ingest")
    if sample_ratio >= 1.0:
        return list(rows)
    n = max(1, int(round(len(rows) * sample_ratio)))
    rng = random.Random(seed)
    return sorted(rng.sample(list(rows), n), key=lambda r: (r["date"], r["id"]))


def build_chunk_entities(
    *,
    sample_ratio: float | None = None,
    clean: bool | None = None,
) -> ChunkEntityBuildResult:
    """
    主路径：先抽每个 chunk 的 entity → 规则+LLM 清洗 → 再合并全局。
    sample_ratio<1 时仅处理子集（试跑用），不覆盖未抽样 chunk 的旧行。
    """
    ratio = resolve_entity_sample_ratio() if sample_ratio is None else sample_ratio
    ratio = max(0.0, min(1.0, float(ratio)))
    db_rows = _select_chunk_rows(sample_ratio=ratio)

    rows: list[ChunkEntityRow] = []
    n_with = 0
    for r in db_rows:
        text = r["text"] or ""
        items = extract_entities_for_text(text)
        if items:
            n_with += 1
        preview = text.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:80] + "…"
        rows.append(
            ChunkEntityRow(
                chunk_id=r["id"],
                date=r["date"],
                entities=items,
                preview=preview,
            )
        )

    review_meta: dict[str, Any] | None = None
    from src.entity_review import resolve_entity_review_config

    review_cfg = resolve_entity_review_config()
    do_clean = clean
    if do_clean is None:
        do_clean = review_cfg.enabled or review_cfg.apply_rule_filters

    if do_clean and rows:
        from src.entity_review import (
            apply_entity_clean_ops,
            review_entities,
            summarize_review,
        )

        print("[entity_review] 规则 + LLM 清洗实体...")
        review = review_entities(rows, review_cfg)
        rows = apply_entity_clean_ops(rows, review)
        review_meta = summarize_review(review)
        print(
            f"  ops={review_meta['n_ops']} rewrite={review_meta['n_rewrite']} "
            f"drop={review_meta['n_drop']} examples={review_meta['examples'][:5]}"
        )
        n_with = sum(1 for r in rows if r.entities)

    global_entities = merge_chunk_entities_to_global(rows)
    return ChunkEntityBuildResult(
        rows=rows,
        global_entities=global_entities,
        n_chunks=len(rows),
        n_with_entities=n_with,
        sample_ratio=ratio,
        review_meta=review_meta,
    )


def save_chunk_entities_db(
    result: ChunkEntityBuildResult,
    *,
    replace_all: bool | None = None,
) -> None:
    """
    写入 diary.db.chunk_entity；同步 chunk_tags.entities。
    replace_all=True（全量）时清空整表再写；抽样时只替换本批 chunk_id。
    """
    if replace_all is None:
        replace_all = result.sample_ratio >= 1.0

    conn = get_db()
    try:
        chunk_ids = [r.chunk_id for r in result.rows]
        if replace_all:
            conn.execute("DELETE FROM chunk_entity")
        elif chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            conn.execute(
                f"DELETE FROM chunk_entity WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )

        for row in result.rows:
            ent_json = json.dumps(
                [
                    {"name": e.name, "entity_type": e.entity_type, "tf": e.tf}
                    for e in row.entities
                ],
                ensure_ascii=False,
            )
            existing = conn.execute(
                "SELECT chunk_id FROM chunk_tags WHERE chunk_id = ?",
                (row.chunk_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE chunk_tags
                    SET entities = ?, extracted_at = datetime('now')
                    WHERE chunk_id = ?
                    """,
                    (ent_json, row.chunk_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO chunk_tags
                    (chunk_id, topics, activities, emotions, food_mentions, people,
                     is_touching_moment, touching_summary, keywords, tag_method, entities)
                    VALUES (?, '[]', '[]', '[]', '[]', '[]', 0, '', '[]', 'tfidf', ?)
                    """,
                    (row.chunk_id, ent_json),
                )

            for e in row.entities:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO chunk_entity
                    (chunk_id, name, entity_type, tf)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row.chunk_id, e.name, e.entity_type, int(e.tf)),
                )
        conn.commit()
    finally:
        conn.close()


def save_chunk_entities_json(
    result: ChunkEntityBuildResult,
    path: Path | None = None,
) -> Path:
    out = path or resolve_path(resolve_chunk_entities_output_path())
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "sample_ratio": result.sample_ratio,
        "n_chunks": result.n_chunks,
        "n_with_entities": result.n_with_entities,
        "review": result.review_meta,
        "chunks": [
            {
                "chunk_id": r.chunk_id,
                "date": r.date,
                "preview": r.preview,
                "entities": [
                    {"name": e.name, "entity_type": e.entity_type, "tf": e.tf}
                    for e in r.entities
                ],
            }
            for r in result.rows
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def save_entities(
    result: EntityBuildResult,
    path: Path | None = None,
    *,
    persist_db: bool = True,
    replace_extracted: bool = True,
) -> Path:
    """写全局 entities.json + lexicon.entities。"""
    out = path or resolve_path(resolve_entities_output_path())
    out.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "n_docs": result.n_docs,
        "n_entities": len(result.records),
        "by_type": result.by_type or {},
        "entities": [asdict(r) for r in result.records],
        "people": [r.name for r in result.records if r.entity_type == "person"],
        "places": [r.name for r in result.records if r.entity_type == "place"],
        "orgs": [r.name for r in result.records if r.entity_type == "org"],
        "source": "merge_chunk_entities",
    }

    if persist_db:
        from src.lexicon_db import replace_or_upsert_entities

        replace_or_upsert_entities(
            [asdict(r) for r in result.records],
            source="extract",
            replace_extracted=replace_extracted,
        )

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def build_and_save_entities(
    *,
    sample_ratio: float | None = None,
    clean: bool | None = None,
) -> tuple[ChunkEntityBuildResult, Path, Path]:
    """chunk 实体 → 清洗 → DB/JSON；合并全局 → lexicon + entities.json。"""
    result = build_chunk_entities(sample_ratio=sample_ratio, clean=clean)
    save_chunk_entities_db(result)
    chunk_path = save_chunk_entities_json(result)
    # 抽样时不 wipe 全库 manual/旧 extract：仅 upsert 本批合并结果
    replace = result.sample_ratio >= 1.0
    global_path = save_entities(
        result.global_entities,
        replace_extracted=replace,
    )
    return result, chunk_path, global_path


# ---------- 兼容旧调用 ----------

def collect_entity_stats(texts: list[str]) -> tuple[list[EntityRecord], int]:
    rows = [
        ChunkEntityRow(
            chunk_id=str(i),
            date="",
            entities=extract_entities_for_text(t),
        )
        for i, t in enumerate(texts)
        if t and t.strip()
    ]
    merged = merge_chunk_entities_to_global(rows)
    return merged.records, merged.n_docs


def build_entities_from_texts(texts: list[str]) -> EntityBuildResult:
    records, n_docs = collect_entity_stats(texts)
    by_type: dict[str, int] = defaultdict(int)
    for r in records:
        by_type[r.entity_type] += 1
    return EntityBuildResult(records=records, n_docs=n_docs, by_type=dict(by_type))


def build_entities_from_db() -> EntityBuildResult:
    """全量：走 chunk→合并；返回全局结果。"""
    return build_chunk_entities(sample_ratio=1.0).global_entities


def load_entity_names(
    *,
    entity_types: list[EntityType] | None = None,
) -> list[str]:
    try:
        from src.lexicon_db import load_entities

        rows = load_entities(entity_types=entity_types)
        if rows:
            return [r["name"] for r in rows]
    except Exception:
        pass

    path = resolve_path(resolve_entities_output_path())
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if entity_types:
        wanted = set(entity_types)
        return [
            e["name"]
            for e in data.get("entities") or []
            if e.get("entity_type") in wanted
        ]
    return list(data.get("people") or []) + list(data.get("places") or []) + list(
        data.get("orgs") or []
    )
