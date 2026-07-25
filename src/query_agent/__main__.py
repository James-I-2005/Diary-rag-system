"""Query Agent CLI 调试。"""

from __future__ import annotations

from src.query_agent import QueryAgent


def main() -> None:
    agent = QueryAgent()
    samples = [
        "你好",
        "碧蓮做了什么？",
        "有没有写过关于藤田君的记录？",
        "我什么时候开始变得自律？",
    ]
    for q in samples:
        sq = agent.process(q)
        print(f"\nQ: {q}")
        print(f"  source={sq.source}")
        print(f"  rewritten: {sq.rewritten_query}")
        print(f"  query_sentences:")
        for s in sq.query_sentences:
            print(f"    - {s}")
        print(f"  embedding_query: {sq.view_retrieval_query()[:120]}")


if __name__ == "__main__":
    main()
