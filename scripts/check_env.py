"""环境检查脚本。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check() -> None:
    errors = []

    if sys.version_info < (3, 11):
        errors.append(f"Python 需 3.11+，当前 {sys.version}")

    for pkg in ["chromadb", "sentence_transformers", "yaml", "rich", "openai"]:
        try:
            if pkg == "yaml":
                __import__("yaml")
            else:
                __import__(pkg)
        except ImportError:
            errors.append(f"缺少依赖: {pkg}")

    try:
        from src.store import load_config

        cfg = load_config()
        print("配置加载成功:", list(cfg.keys()))
    except Exception as e:
        errors.append(f"配置加载失败: {e}")

    if errors:
        print("❌ 环境问题:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("✅ 环境就绪")


if __name__ == "__main__":
    check()
