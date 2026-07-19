"""Retrieval Plan：仅描述 Operator 执行顺序。"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.operator import Operator


@dataclass
class Plan:
    operators: list[Operator] = field(default_factory=list)

    def __iter__(self):
        return iter(self.operators)

    def __len__(self) -> int:
        return len(self.operators)
