"""召回工具：纯函数，无框架。由 Query Agent 按名调用。"""

from __future__ import annotations

from typing import Any, Callable

from src.tools.grep import grep_chunks
from src.tools.rag_search import rag_search

ToolFn = Callable[..., dict[str, Any]]

REGISTRY: dict[str, ToolFn] = {
    "grep": grep_chunks,
    "rag_search": rag_search,
}


def call_tool(name: str, **kwargs: Any) -> dict[str, Any]:
    """按名称调用工具；未知名称返回 error 结构，不抛给上层炸穿。"""
    fn = REGISTRY.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown_tool:{name}", "chunks": [], "hits": []}
    try:
        out = fn(**kwargs)
        if not isinstance(out, dict):
            return {"ok": False, "error": "bad_tool_return", "chunks": [], "hits": []}
        out.setdefault("ok", True)
        out.setdefault("tool", name)
        return out
    except Exception as exc:
        return {
            "ok": False,
            "tool": name,
            "error": str(exc),
            "chunks": [],
            "hits": [],
        }


def list_tools() -> list[str]:
    return sorted(REGISTRY.keys())
