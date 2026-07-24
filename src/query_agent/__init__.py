"""Query Agent：理解用户意图，输出 StructuredQuery。"""

from src.query_agent.models import QueryRepresentation, StructuredQuery

__all__ = ["QueryAgent", "QueryRepresentation", "StructuredQuery"]


def __getattr__(name: str):
    if name == "QueryAgent":
        from src.query_agent.agent import QueryAgent

        return QueryAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
