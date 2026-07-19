"""Memory Retrieval Engine：Candidate / Operator / Plan / PlanExecutor。"""

from __future__ import annotations

from src.engine.candidate import Candidate, merge_candidates
from src.engine.executor import PlanExecutor
from src.engine.operator import Operator
from src.engine.plan import Plan
from src.engine.registry import build_plan, build_plan_from_config, resolve_plan_names

__all__ = [
    "Candidate",
    "Operator",
    "Plan",
    "PlanExecutor",
    "merge_candidates",
    "build_plan",
    "build_plan_from_config",
    "resolve_plan_names",
]


def run_plan(query: str, plan: Plan | None = None) -> list[Candidate]:
    """便捷入口：执行默认或指定 Plan。"""
    p = plan or build_plan_from_config()
    return PlanExecutor().run(query, p)


if __name__ == "__main__":
    import json

    q = "碧蓮做了什么"
    plan = build_plan_from_config()
    print("plan:", [op.name for op in plan.operators])
    hits = PlanExecutor().run(q, plan)
    print(f"query={q!r} n={len(hits)}")
    print(
        json.dumps(
            [{"chunk_id": c.chunk_id, "score": c.score, "source": c.source} for c in hits[:5]],
            ensure_ascii=False,
            indent=2,
        )
    )
