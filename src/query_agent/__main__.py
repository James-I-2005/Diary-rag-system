"""Query Agent CLI 调试。"""

from __future__ import annotations

from src.query_agent import QueryAgent


def main() -> None:
    agent = QueryAgent()
    samples = [
        "你好",
        "谢谢！",
        "碧蓮做了什么？",
        "有没有写过关于藤田君的记录？",
        "后来呢？",
    ]
    for q in samples:
        sq = agent.process(q)
        print(f"\nQ: {q}")
        print(f"  need_retrieval={sq.need_retrieval} intent={sq.intent} source={sq.source}")
        print(f"  rewritten: {sq.rewritten_query}")
        print(f"  plan: {sq.retrieval_plan}")


if __name__ == "__main__":
    main()
