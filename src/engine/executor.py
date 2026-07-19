"""PlanExecutor：按 Plan 顺序执行 Operator，传递 CandidateSet。"""

from __future__ import annotations

from src.engine.candidate import Candidate
from src.engine.plan import Plan


class PlanExecutor:
    def run(self, query: str, plan: Plan) -> list[Candidate]:
        candidates: list[Candidate] = []
        for op in plan.operators:
            candidates = op.execute(query=query, candidates=candidates)
        return candidates
