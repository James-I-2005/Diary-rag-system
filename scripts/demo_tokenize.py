"""演示 jieba 分词 + 实体抽取。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tokenize import analyze_question, analyze_text


def main() -> None:
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
        items = [("输入", text)]
    else:
        items = [
            ("chunk", "我和 Jenny 在滨江散步，全程只提了她一次名字。"),
            ("chunk", "與張小姐一同賞月，前往天津，與開灤白川氏會面。"),
            (
                "question",
                "我和 Jenny 去约会的那次回忆",
            ),
        ]

    for label, text in items:
        fn = analyze_question if label == "question" else analyze_text
        a = fn(text)
        print(f"\n[{label}] {text}")
        print(f"  tokens : {a.tokens}")
        print(f"  people : {a.people}")
        print(f"  places : {a.places}")
        print(f"  orgs   : {a.orgs}")
        print(f"  entities: {a.entities}")


if __name__ == "__main__":
    main()
