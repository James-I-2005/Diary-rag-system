"""检索配置与问题侧标签抽取（调试用）。原 TagOperator / tag_match 已退役。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from src.store import load_config
from src.tokenize import analyze_question
from src.vocabulary import load_vocabulary_terms


@dataclass
class RetrievalConfig:
    top_k: int = 20
    w_vector: float = 1.0


@dataclass
class QuerySideTags:
    entities: list[str]
    keywords: list[str]
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def resolve_retrieval_config() -> RetrievalConfig:
    cfg = load_config().get("retrieval") or {}
    return RetrievalConfig(
        top_k=_env_int("RETRIEVAL_TOP_K", int(cfg.get("top_k", 20))),
        w_vector=_env_float("RETRIEVAL_W_VECTOR", float(cfg.get("w_vector", 1.0))),
    )


def extract_query_tags(question: str) -> QuerySideTags:
    """
    问题侧：实体（出现即用）+ 关键词（与全局 V 求交，排除已是实体的）。
    供 retrieval debug / Context 元数据，不再驱动 TagOperator。
    """
    analysis = analyze_question(question)
    entities = list(analysis.entities)
    entity_set = set(entities)

    try:
        vocab = set(load_vocabulary_terms())
    except Exception:
        vocab = set()

    keywords: list[str] = []
    for t in analysis.tokens:
        if t in entity_set:
            continue
        if vocab and t not in vocab:
            continue
        if t not in keywords:
            keywords.append(t)

    if not vocab:
        keywords = [t for t in analysis.tokens if t not in entity_set]

    return QuerySideTags(
        entities=entities,
        keywords=keywords,
        people=list(analysis.people),
        places=list(analysis.places),
        orgs=list(analysis.orgs),
    )
