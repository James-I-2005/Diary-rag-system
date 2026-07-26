"""Query Agent CLI 调试。"""

from __future__ import annotations

from src.query_agent import QueryAgent


def main() -> None:
    agent = QueryAgent()
    samples = [
        "你好",
        "我记得我曾经看过一个和非洲相关的视频 当时我学到了什么",
        "那次打羽毛球被小胖墩夸了后来怎么样",
    ]
    for q in samples:
        sq = agent.process(q)
        print(f"\nQ: {q}")
        print(f"  source={sq.source}")
        print(f"  rewritten: {sq.rewritten_query}")
        print(f"  themes:")
        for t in sq.query_themes:
            print(f"    - {t}")


if __name__ == "__main__":
    main()
