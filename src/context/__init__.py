"""Context Engine：会话短期记忆（滑动窗口 + 摘要）+ 本轮召回 → LLM Context。"""

from __future__ import annotations

from src.context.conversation import ConversationManager
from src.context.engine import ContextEngine
from src.context.models import (
    BuiltContext,
    ConversationState,
    Message,
    RetrievedMemory,
)
from src.context.tokens import TokenBudget, estimate_tokens, resolve_token_budget

__all__ = [
    "BuiltContext",
    "ContextEngine",
    "ContextService",
    "ConversationManager",
    "ConversationState",
    "Message",
    "RetrievedMemory",
    "TokenBudget",
    "estimate_tokens",
    "resolve_token_budget",
]


def __getattr__(name: str):
    if name == "ContextService":
        from src.context.service import ContextService

        return ContextService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    from src.context.service import ContextService

    svc = ContextService()
    r1 = svc.handle_turn("碧蓮做了什么", use_vector=False)
    print("cid:", r1["conversation_id"])
    print("plan:", r1["plan"])
    print("memories:", r1["memories_used"][:3])
    print("answer:", (r1["answer"] or "")[:200])
    r2 = svc.handle_turn(
        "那之后呢？",
        conversation_id=r1["conversation_id"],
        use_vector=False,
    )
    print("\n--- turn2 ---")
    print("answer:", (r2["answer"] or "")[:200])
    print("preview roles:", r2["context_messages_preview"])
