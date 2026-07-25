"""命名检索方案：operators + 合并策略（max / weighted）。v0.4 已移除 view。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from src.engine.candidate import (
    Candidate,
    merge_candidates,
    merge_candidates_weighted,
    merge_candidates_weighted_paths,
)
from src.engine.registry import create_operator
from src.store import load_config
from src.tag_retrieve import resolve_retrieval_config

_BUILTIN_SCHEMES: dict[str, dict[str, Any]] = {
    "weighted_50_50": {
        "label": "Tag + Sentence 加权 (0.5/0.5)",
        "description": "Tag（展开 sentence）+ rag-sentence 向量加权",
        "operators": ["tag", "embedding"],
        "merge": "weighted",
        "w_tag": 0.5,
        "w_embedding": 0.5,
    },
    "union_max": {
        "label": "并集取 max",
        "description": "tag + embedding 并集取较大分",
        "operators": ["tag", "embedding"],
        "merge": "max",
    },
    "tag_only": {
        "label": "仅 Tag",
        "description": "实体/关键词 → 展开 sentence",
        "operators": ["tag"],
        "merge": "max",
    },
    "embedding_only": {
        "label": "仅 Sentence 向量",
        "description": "只走 rag-sentence ANN",
        "operators": ["embedding"],
        "merge": "max",
    },
}

# 已退役方案 id → 回退
_DEPRECATED_SCHEME_ALIASES = {
    "tag_view_weighted": "weighted_50_50",
    "view_only": "embedding_only",
    "triple_max": "union_max",
}


@dataclass
class RetrievalScheme:
    id: str
    label: str
    description: str = ""
    operators: list[str] = field(default_factory=list)
    merge: str = "max"
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
    ops = [str(n).strip().lower() for n in ops if str(n).strip() and str(n).strip().lower() != "view"]
    if not ops:
        ops = ["tag", "embedding"]
    return RetrievalScheme(
        id=sid,
        label=str(raw.get("label") or sid),
        description=str(raw.get("description") or ""),
        operators=ops,
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
            if k in _DEPRECATED_SCHEME_ALIASES:
                continue
            if isinstance(v, dict):
                ops = v.get("operators") or []
                if isinstance(ops, str):
                    ops = [n.strip() for n in ops.split(",")]
                if any(str(n).strip().lower() == "view" for n in ops):
                    continue
                base = dict(merged.get(k) or {})
                base.update(v)
                merged[k] = base
    order = list(_BUILTIN_SCHEMES.keys())
    for k in merged:
        if k not in order:
            order.append(k)
    return [_parse_scheme(k, merged[k]) for k in order if k in merged]


def resolve_default_scheme_id() -> str:
    env = os.getenv("RETRIEVAL_SCHEME", "").strip()
    if env:
        return _DEPRECATED_SCHEME_ALIASES.get(env, env)
    cfg = load_config().get("retrieval") or {}
    sid = str(cfg.get("scheme") or "weighted_50_50")
    return _DEPRECATED_SCHEME_ALIASES.get(sid, sid)


def get_scheme(scheme_id: str | None = None) -> RetrievalScheme:
    sid = (scheme_id or resolve_default_scheme_id()).strip()
    sid = _DEPRECATED_SCHEME_ALIASES.get(sid, sid)
    by_id = {s.id: s for s in list_schemes()}
    if sid in by_id:
        return by_id[sid]
    if "," in sid or sid in {"tag", "embedding"}:
        ops = [n.strip().lower() for n in sid.split(",") if n.strip() and n.strip() != "view"]
        return RetrievalScheme(id=sid, label=sid, operators=ops or ["embedding"], merge="max")
    default_id = resolve_default_scheme_id()
    if default_id in by_id:
        return by_id[default_id]
    return list_schemes()[0]


def _resolve_op_query(name: str, query: str, structured: Any) -> str:
    if structured is not None and name == "embedding":
        vq = getattr(structured, "view_retrieval_query", None)
        if callable(vq):
            text = vq()
            if text.strip():
                return text.strip()
        eq = getattr(structured, "embedding_query", "") or ""
        if eq.strip():
            return eq.strip()
    if structured is not None and name == "tag":
        rq = getattr(structured, "retrieval_query", None)
        if callable(rq):
            text = rq()
            if text.strip():
                return text.strip()
    return query


def run_scheme(
    query: str,
    scheme: RetrievalScheme | str | None = None,
    *,
    structured: Any = None,
    top_k: int | None = None,
) -> tuple[list[Candidate], RetrievalScheme]:
    sch = scheme if isinstance(scheme, RetrievalScheme) else get_scheme(scheme)
    cfg = resolve_retrieval_config()
    k = top_k if top_k is not None else cfg.top_k

    per_op: dict[str, list[Candidate]] = {}
    for name in sch.operators:
        if name == "view":
            continue
        op = create_operator(name, top_k=k)
        op_query = _resolve_op_query(name, query, structured)
        per_op[name] = op.execute(
            query=op_query, candidates=[], structured=structured
        )

    if sch.merge == "weighted" and len(sch.operators) >= 2:
        op_set = set(n for n in sch.operators if n != "view")
        if op_set == {"tag", "embedding"}:
            merged = merge_candidates_weighted(
                per_op.get("tag") or [],
                per_op.get("embedding") or [],
                w_tag=sch.w_tag,
                w_embedding=sch.w_embedding,
                top_k=k,
            )
        else:
            weights = {}
            paths = {}
            for name in sch.operators:
                if name == "view":
                    continue
                w = getattr(sch, f"w_{name}", 1.0 / max(len(op_set), 1))
                weights[name] = w
                paths[name] = per_op.get(name) or []
            merged = merge_candidates_weighted_paths(paths, weights, top_k=k)
        return merged, sch

    candidates: list[Candidate] = []
    for name in sch.operators:
        if name == "view":
            continue
        candidates = merge_candidates(candidates, per_op.get(name) or [])
    return candidates[:k], sch
