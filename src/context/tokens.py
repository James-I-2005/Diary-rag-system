"""粗粒度 token 估算与预算分配（中文按字符近似，可配置）。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.store import load_config


@dataclass
class TokenBudget:
    total: int = 8000
    system_ratio: float = 0.10
    summary_ratio: float = 0.15
    recent_ratio: float = 0.25
    memories_ratio: float = 0.40
    query_ratio: float = 0.10

    def allot(self, key: str) -> int:
        ratios = {
            "system": self.system_ratio,
            "summary": self.summary_ratio,
            "recent": self.recent_ratio,
            "memories": self.memories_ratio,
            "query": self.query_ratio,
        }
        r = ratios.get(key, 0.0)
        return max(0, int(self.total * r))


def estimate_tokens(text: str) -> int:
    """中英混合粗估：约按字符 * 0.9。"""
    if not text:
        return 0
    return max(1, int(len(text) * 0.9) + 1)


def resolve_token_budget() -> TokenBudget:
    cfg = (load_config().get("context") or {}).get("budget") or {}
    ctx = load_config().get("context") or {}
    total_env = os.getenv("CONTEXT_TOKEN_BUDGET", "").strip()
    return TokenBudget(
        total=int(total_env) if total_env else int(ctx.get("total_token_budget", 8000)),
        system_ratio=float(
            os.getenv("CONTEXT_BUDGET_SYSTEM", "").strip() or cfg.get("system", 0.10)
        ),
        summary_ratio=float(
            os.getenv("CONTEXT_BUDGET_SUMMARY", "").strip() or cfg.get("summary", 0.15)
        ),
        recent_ratio=float(
            os.getenv("CONTEXT_BUDGET_RECENT", "").strip() or cfg.get("recent", 0.25)
        ),
        memories_ratio=float(
            os.getenv("CONTEXT_BUDGET_MEMORIES", "").strip() or cfg.get("memories", 0.40)
        ),
        query_ratio=float(
            os.getenv("CONTEXT_BUDGET_QUERY", "").strip() or cfg.get("query", 0.10)
        ),
    )


def fit_text(text: str, max_tokens: int) -> str:
    if max_tokens <= 0 or not text:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    keep = max(1, int(max_tokens / 0.9))
    if len(text) <= keep:
        return text
    return text[: keep - 1] + "…"
