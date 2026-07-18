"""环境检查脚本。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check() -> None:
    errors = []
    warnings = []

    if sys.version_info < (3, 11):
        errors.append(f"Python 需 3.11+，当前 {sys.version}")

    for pkg in ["chromadb", "sentence_transformers", "yaml", "rich", "openai", "dotenv"]:
        try:
            if pkg == "yaml":
                __import__("yaml")
            elif pkg == "dotenv":
                __import__("dotenv")
            else:
                __import__(pkg)
        except ImportError:
            errors.append(f"缺少依赖: {pkg}")

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        from src.store import load_config

        from src.store import resolve_diary_dir

        cfg = load_config()
        print("配置加载成功:", list(cfg.keys()))
        print("日记目录:", resolve_diary_dir())
        llm = cfg.get("llm") or {}
        print("LLM 角色:", list(llm.keys()))
    except Exception as e:
        errors.append(f"配置加载失败: {e}")
        llm = {}

    # OpenRouter key：tags/answer 若走 openrouter 且 api_key=env，则提示检查
    need_or = False
    for role, section in (llm or {}).items():
        if not isinstance(section, dict):
            continue
        provider = str(section.get("provider", "")).lower()
        if provider == "openrouter":
            need_or = True
            env_name = section.get("api_key_env") or "OPENROUTER_API_KEY"
            if not os.getenv(env_name):
                warnings.append(
                    f"llm.{role} 使用 openrouter，但未设置环境变量 {env_name}（可复制 .env.example → .env）"
                )

    if need_or and not warnings:
        print("OpenRouter API Key: 已检测到")

    if errors:
        print("❌ 环境问题:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("✅ 环境就绪")

    if warnings:
        print("⚠️ 提醒:")
        for w in warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    check()
