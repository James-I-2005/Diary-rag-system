"""命名检索方案：operators + 合并策略（max / weighted）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from src.engine.candidate import Candidate, merge_candidates, merge_candidates_weighted
from src.engine.registry import create_operator
from src.store import load_config
from src.tag_retrieve import resolve_retrieval_config

# 内置方案（config.retrieval.schemes 可覆盖同名项）
_BUILTIN_SCHEMES: dict[str, dict[str, Any]] = {
    "weighted_50_50": {
        "label": "Tag + RAG 加权 (0.5/0.5)",
        "description": "两路各自归一化后按 0.5/0.5 加权求和",
        "operators": ["tag", "embedding"],
        "merge": "weighted",
        "w_tag": 0.5,
        "w_embedding": 0.5,
    },
    "union_max": {
        "label": "并集取 max（旧默认）",
        "description": "tag → embedding 顺序执行，同分取 max",
        "operators": ["tag", "embedding"],
        "merge": "max",
    },
    "tag_only": {
        "label": "仅 Tag",
        "description": "只走实体/关键词倒排",
        "operators": ["tag"],
        "merge": "max",
    },
    "embedding_only": {
        "label": "仅 RAG（向量）",
        "description": "只走 embedding 语义检索",
        "operators": ["embedding"],
        "merge": "max",
    },
}


@dataclass
class RetrievalScheme:
    id: str
    label: str
    description: str = ""
    operators: list[str] = field(default_factory=list)
    merge: str = "max"  # max | weighted
    w_tag: float = 0.5
    w_embedding: float = 0.5

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "operators": list(self.operators),
            "merge": self.merge,
            "w_tag": self.w_tag,
            "w_embedding": self.w_embedding,
        }


def _parse_scheme(sid: str, raw: dict[str, Any]) -> RetrievalScheme:
    merge = str(raw.get("merge") or "max").strip().lower()
    if merge not in {"max", "weighted"}:
        merge = "max"
    ops = raw.get("operators") or ["tag", "embedding"]
    if isinstance(ops, str):
        ops = [n.strip() for n in ops.split(",") if n.strip()]
    return RetrievalScheme(
        id=sid,
        label=str(raw.get("label") or sid),
        description=str(raw.get("description") or ""),
        operators=[str(n).strip().lower() for n in ops if str(n).strip()],
        merge=merge,
        w_tag=float(raw.get("w_tag", 0.5)),
        w_embedding=float(raw.get("w_embedding", raw.get("w_vector", 0.5))),
    )


def list_schemes() -> list[RetrievalScheme]:
    cfg = load_config().get("retrieval") or {}
    custom = cfg.get("schemes") or {}
    merged: dict[str, dict[str, Any]] = {**_BUILTIN_SCHEMES}
    if isinstance(custom, dict):
        for k, v in custom.items():
            if isinstance(v, dict):
                base = dict(merged.get(k) or {})
                base.update(v)
                merged[k] = base
    # 保持内置顺序，再追加仅自定义的
    order = list(_BUILTIN_SCHEMES.keys())
    for k in merged:
        if k not in order:
            order.append(k)
    return [_parse_scheme(k, merged[k]) for k in order if k in merged]


def resolve_default_scheme_id() -> str:
    env = os.getenv("RETRIEVAL_SCHEME", "").strip()
    if env:
        return env
    cfg = load_config().get("retrieval") or {}
    return str(cfg.get("scheme") or "weighted_50_50")


def get_scheme(scheme_id: str | None = None) -> RetrievalScheme:
    sid = (scheme_id or resolve_default_scheme_id()).strip()
    by_id = {s.id: s for s in list_schemes()}
    if sid in by_id:
        return by_id[sid]
    # 兼容：把 "tag,embedding" 当作 union_max 风格临时方案
    if "," in sid or sid in {"tag", "embedding"}:
        ops = [n.strip().lower() for n in sid.split(",") if n.strip()]
        return RetrievalScheme(
            id=sid,
            label=sid,
            operators=ops,
            merge="max",
        )
    # 未知 → 默认
    default_id = resolve_default_scheme_id()
    if default_id in by_id:
        return by_id[default_id]
    return list_schemes()[0]


def run_scheme(
    query: str,
    scheme: RetrievalScheme | str | None = None,
    *,
    top_k: int | None = None,
) -> tuple[list[Candidate], RetrievalScheme]:
    """按方案独立跑各 Operator，再按 merge 策略合并。"""
    sch = scheme if isinstance(scheme, RetrievalScheme) else get_scheme(scheme)
    cfg = resolve_retrieval_config()
    k = top_k if top_k is not None else cfg.top_k

    per_op: dict[str, list[Candidate]] = {}
    for name in sch.operators:
        op = create_operator(name, top_k=k)
        # 独立执行：不把上一算子结果传入，避免 max 链式污染加权
        per_op[name] = op.execute(query=query, candidates=[])

    if sch.merge == "weighted" and len(sch.operators) >= 2:
        tag_hits = per_op.get("tag") or []
        emb_hits = per_op.get("embedding") or []
        # 若方案含其他算子，先 max 并入对应侧或单独并
        others = [
            c
            for n, hits in per_op.items()
            if n not in {"tag", "embedding"}
            for c in hits
        ]
        merged = merge_candidates_weighted(
            tag_hits,
            emb_hits,
            w_tag=sch.w_tag,
            w_embedding=sch.w_embedding,
            top_k=k,
        )
        if others:
            merged = merge_candidates(merged, others)[:k]
        return merged, sch

    # max：顺序并集取 max（与旧 PlanExecutor 行为一致）
    candidates: list[Candidate] = []
    for name in sch.operators:
        candidates = merge_candidates(candidates, per_op.get(name) or [])
    return candidates[:k], sch
