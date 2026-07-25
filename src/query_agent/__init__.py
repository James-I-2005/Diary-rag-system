"""Query Agent：改写用户问题并拆成 query rag-sentences。"""

from src.query_agent.models import StructuredQuery

__all__ = ["QueryAgent", "StructuredQuery"]


def __getattr__(name: str):
    if name == "QueryAgent":
        from src.query_agent.agent import QueryAgent

        return QueryAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
