"""从 config / 环境变量构建 Plan。"""

from __future__ import annotations

import os

from src.engine.operator import Operator
from src.engine.operators.embedding import EmbeddingOperator
from src.engine.plan import Plan
from src.store import load_config
from src.tag_retrieve import resolve_retrieval_config

_REGISTRY: dict[str, type[Operator]] = {
    "embedding": EmbeddingOperator,
}


def resolve_plan_names() -> list[str]:
    """默认 ["embedding"]；可用 RETRIEVAL_PLAN=embedding 覆盖。"""
    env = os.getenv("RETRIEVAL_PLAN", "").strip()
    if env:
        names = [n.strip().lower() for n in env.split(",") if n.strip()]
        names = [n for n in names if n not in {"view", "tag"}]
        if names:
            return names
    cfg = load_config().get("retrieval") or {}
    raw = cfg.get("plan") or ["embedding"]
    if isinstance(raw, str):
        names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    else:
        names = [str(n).strip().lower() for n in raw if str(n).strip()]
    return [n for n in names if n not in {"view", "tag"}]


def create_operator(name: str, *, top_k: int | None = None) -> Operator:
    key = name.strip().lower()
    if key in {"view", "tag"}:
        raise KeyError(f"Operator {name!r} 已退役，请使用 embedding")
    cls = _REGISTRY.get(key)
    if cls is None:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"未知 Operator: {name!r}（可选: {known}）")
    return cls(top_k=top_k)  # type: ignore[call-arg]


def build_plan(
    names: list[str] | None = None,
    *,
    top_k: int | None = None,
) -> Plan:
    names = names or resolve_plan_names()
    names = [n for n in names if n not in {"view", "tag"}]
    if not names:
        names = ["embedding"]
    k = top_k if top_k is not None else resolve_retrieval_config().top_k
    ops = [create_operator(n, top_k=k) for n in names]
    return Plan(operators=ops)


def build_plan_from_config(*, top_k: int | None = None) -> Plan:
    return build_plan(resolve_plan_names(), top_k=top_k)
