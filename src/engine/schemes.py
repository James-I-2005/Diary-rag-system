"""命名检索方案：operators + 合并策略（max / weighted）。v0.5 仅保留 embedding。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from src.engine.candidate import (
    Candidate,
    merge_candidates,
    merge_candidates_weighted_paths,
)
from src.engine.registry import create_operator
from src.store import load_config
from src.tag_retrieve import resolve_retrieval_config

_BUILTIN_SCHEMES: dict[str, dict[str, Any]] = {
    "embedding_only": {
        "label": "仅 Sentence 向量",
        "description": "只走 rag-sentence ANN",
        "operators": ["embedding"],
        "merge": "max",
    },
}

# 已退役方案 id → 回退
_DEPRECATED_SCHEME_ALIASES = {
    "tag_view_weighted": "embedding_only",
    "view_only": "embedding_only",
    "triple_max": "embedding_only",
    "weighted_50_50": "embedding_only",
    "union_max": "embedding_only",
    "tag_only": "embedding_only",
}


@dataclass
class RetrievalScheme:
    id: str
    label: str
    description: str = ""
    operators: list[str] = field(default_factory=list)
    merge: str = "max"
    w_embedding: float = 1.0

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "operators": list(self.operators),
            "merge": self.merge,
            "w_embedding": self.w_embedding,
        }


def _parse_scheme(sid: str, raw: dict[str, Any]) -> RetrievalScheme:
    merge = str(raw.get("merge") or "max").strip().lower()
    if merge not in {"max", "weighted"}:
        merge = "max"
    ops = raw.get("operators") or ["embedding"]
    if isinstance(ops, str):
        ops = [n.strip() for n in ops.split(",") if n.strip()]
    ops = [
        str(n).strip().lower()
        for n in ops
        if str(n).strip() and str(n).strip().lower() not in {"view", "tag"}
    ]
    if not ops:
        ops = ["embedding"]
    return RetrievalScheme(
        id=sid,
        label=str(raw.get("label") or sid),
        description=str(raw.get("description") or ""),
        operators=ops,
        merge=merge,
        w_embedding=float(raw.get("w_embedding", raw.get("w_vector", 1.0))),
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
                if any(str(n).strip().lower() in {"view", "tag"} for n in ops):
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
    sid = str(cfg.get("scheme") or "embedding_only")
    return _DEPRECATED_SCHEME_ALIASES.get(sid, sid)


def get_scheme(scheme_id: str | None = None) -> RetrievalScheme:
    sid = (scheme_id or resolve_default_scheme_id()).strip()
    sid = _DEPRECATED_SCHEME_ALIASES.get(sid, sid)
    by_id = {s.id: s for s in list_schemes()}
    if sid in by_id:
        return by_id[sid]
    if "," in sid or sid == "embedding":
        ops = [
            n.strip().lower()
            for n in sid.split(",")
            if n.strip() and n.strip().lower() not in {"view", "tag"}
        ]
        return RetrievalScheme(
            id=sid, label=sid, operators=ops or ["embedding"], merge="max"
        )
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
        if name in {"view", "tag"}:
            continue
        op = create_operator(name, top_k=k)
        op_query = _resolve_op_query(name, query, structured)
        per_op[name] = op.execute(
            query=op_query, candidates=[], structured=structured
        )

    if sch.merge == "weighted" and len(sch.operators) >= 2:
        op_set = {n for n in sch.operators if n not in {"view", "tag"}}
        weights = {}
        paths = {}
        for name in sch.operators:
            if name in {"view", "tag"}:
                continue
            w = getattr(sch, f"w_{name}", 1.0 / max(len(op_set), 1))
            weights[name] = w
            paths[name] = per_op.get(name) or []
        merged = merge_candidates_weighted_paths(paths, weights, top_k=k)
        return merged, sch

    candidates: list[Candidate] = []
    for name in sch.operators:
        if name in {"view", "tag"}:
            continue
        candidates = merge_candidates(candidates, per_op.get(name) or [])
    return candidates[:k], sch
