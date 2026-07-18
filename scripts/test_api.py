"""测试 OpenRouter（或其它 llm.* 后端）API 是否通畅。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def probe_role(role: str) -> bool:
    from src.llm import get_llm_client, get_llm_model, resolve_llm_section

    section = resolve_llm_section(role)
    model = get_llm_model(role)
    client = get_llm_client(role)

    print(f"\n[{role}]")
    print(f"  provider : {section.get('provider')}")
    print(f"  base_url : {section.get('base_url')}")
    print(f"  model    : {model}")
    key = section.get("api_key", "")
    masked = (key[:8] + "…" + key[-4:]) if len(key) > 12 else "***"
    print(f"  api_key  : {masked}")

    t0 = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": '只回复两个字："通畅"',
                }
            ],
            temperature=0,
            max_tokens=32,
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"  ❌ 失败 ({elapsed:.2f}s): {e}")
        return False

    elapsed = time.perf_counter() - t0
    content = (response.choices[0].message.content or "").strip()
    print(f"  ✅ 成功 ({elapsed:.2f}s)")
    print(f"  reply    : {content[:120]!r}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="探测 LLM API 连通性")
    parser.add_argument(
        "roles",
        nargs="*",
        default=["tags", "answer"],
        help="要测试的 llm 角色，默认 tags answer",
    )
    args = parser.parse_args()

    # 去重且保持顺序
    roles: list[str] = []
    for r in args.roles:
        if r not in roles:
            roles.append(r)

    print("探测 LLM API …")
    ok_all = True
    for role in roles:
        try:
            ok = probe_role(role)
        except Exception as e:
            print(f"\n[{role}]")
            print(f"  ❌ 配置/客户端错误: {e}")
            ok = False
        ok_all = ok_all and ok

    print()
    if ok_all:
        print("✅ 全部角色 API 通畅")
        sys.exit(0)

    print("❌ 存在失败角色，请检查 .env / 网络 / OpenRouter 余额与模型名")
    sys.exit(1)


if __name__ == "__main__":
    main()
