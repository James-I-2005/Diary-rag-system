"""Operator 抽象：CandidateSet -> CandidateSet。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.engine.candidate import Candidate


class Operator(ABC):
    """独立、可插拔的检索算子。"""

    name: str = "operator"

    @abstractmethod
    def execute(
        self,
        query: str,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        ...
