"""回答生成：经 Context Engine 构图后调用 LLM（不再做三类问题路由分支）。"""

from __future__ import annotations

from src.context import ContextService

# 模块级默认会话（CLI chat 多轮复用）
_default_service: ContextService | None = None
_default_conversation_id: str | None = None


def _service() -> ContextService:
    global _default_service
    if _default_service is None:
        _default_service = ContextService()
    return _default_service


def reset_conversation() -> str:
    """开启新会话，返回 conversation_id。"""
    global _default_conversation_id
    _default_conversation_id = _service().conversation.create()
    return _default_conversation_id


def generate_answer(
    question: str,
    *,
    conversation_id: str | None = None,
    use_vector: bool = True,
    plan_names: list[str] | None = None,
    scheme: str | None = None,
) -> str:
    """
    一轮问答：Memory Engine 召回（临时）→ Context 构图 → LLM。
    Retrieved memories 不会写入 conversation history。
    """
    global _default_conversation_id
    cid = conversation_id or _default_conversation_id
    result = _service().handle_turn(
        question,
        conversation_id=cid,
        use_vector=use_vector,
        plan_names=plan_names,
        scheme=scheme,
        persist=True,
    )
    _default_conversation_id = result["conversation_id"]
    return result["answer"]


def generate_answer_detailed(
    question: str,
    *,
    conversation_id: str | None = None,
    use_vector: bool = True,
    plan_names: list[str] | None = None,
    scheme: str | None = None,
) -> dict:
    """同 generate_answer，返回完整诊断字段。"""
    global _default_conversation_id
    cid = conversation_id or _default_conversation_id
    result = _service().handle_turn(
        question,
        conversation_id=cid,
        use_vector=use_vector,
        plan_names=plan_names,
        scheme=scheme,
        persist=True,
    )
    _default_conversation_id = result["conversation_id"]
    return result


def generate_answer_stream(question: str):
    """兼容旧 CLI：流式暂降级为整段输出。"""
    print(generate_answer(question))
