"""Query Agent：rewrite 主题改写，或 react 中枢召回。"""

from src.query_agent.models import StructuredQuery

__all__ = [
    "QueryAgent",
    "StructuredQuery",
    "ReactQueryAgent",
    "AgentRetrievalResult",
]


def __getattr__(name: str):
    if name == "QueryAgent":
        from src.query_agent.agent import QueryAgent

        return QueryAgent
    if name == "ReactQueryAgent":
        from src.query_agent.react_agent import ReactQueryAgent

        return ReactQueryAgent
    if name == "AgentRetrievalResult":
        from src.query_agent.react_agent import AgentRetrievalResult

        return AgentRetrievalResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
