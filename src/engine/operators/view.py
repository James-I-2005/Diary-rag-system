"""已退役：v0.4 起由 rag-sentence + EmbeddingOperator 取代。

请勿再注册本 Operator。保留文件仅为避免旧 import 路径报错。
"""

from __future__ import annotations

from src.engine.candidate import Candidate
from src.engine.operator import Operator


class ViewOperator(Operator):
    name = "view"

    def __init__(self, top_k: int | None = None):
        self.top_k = top_k

    def execute(
        self,
        query: str,
        candidates: list[Candidate],
        *,
        structured=None,
    ) -> list[Candidate]:
        print("  [warn] ViewOperator 已在 v0.4 退役，返回空结果")
        return list(candidates)
