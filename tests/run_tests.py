"""运行 test_queries.yaml 中的测试用例。"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.query import classify_query, query


def load_tests() -> list[dict]:
    path = Path(__file__).parent / "test_queries.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_retrieval(result: dict, expect: dict) -> list[str]:
    errors = []
    chunks = result.get("chunks", [])
    count = len(chunks)

    if expect.get("min_results") and count < expect["min_results"]:
        errors.append(f"结果数 {count} < 期望最少 {expect['min_results']}")
    if expect.get("max_results") is not None and count > expect["max_results"]:
        errors.append(f"结果数 {count} > 期望最多 {expect['max_results']}")

    dates = {c["date"] for c in chunks}
    for d in expect.get("must_contain_dates", []):
        if d not in dates:
            errors.append(f"缺少期望日期 {d}")

    all_text = " ".join(c["text"] for c in chunks)
    for kw in expect.get("keywords_in_results", []):
        if kw not in all_text:
            errors.append(f"结果中未找到关键词 '{kw}'")

    return errors


def check_statistical(result: dict, expect: dict) -> list[str]:
    errors = []
    count = result.get("count", -1)

    if expect.get("metric") and result.get("metric") != expect["metric"]:
        errors.append(f"指标 {result.get('metric')} != 期望 {expect['metric']}")
    if expect.get("count_min") is not None and count < expect["count_min"]:
        errors.append(f"计数 {count} < 最小期望 {expect['count_min']}")
    if expect.get("count_max") is not None and count > expect["count_max"]:
        errors.append(f"计数 {count} > 最大期望 {expect['count_max']}")

    return errors


def check_summarization(result: dict, expect: dict) -> list[str]:
    errors = []
    top_acts = [a for a, _ in result.get("top_activities", [])]
    expected_any = expect.get("top_activity_contains", [])
    if expected_any and not any(a in top_acts for a in expected_any):
        # 也接受子串匹配（如「吃火锅」包含「火锅」）
        flat = " ".join(top_acts)
        if not any(a in flat for a in expected_any):
            errors.append(f"top activities {top_acts} 未包含期望词 {expected_any}")
    return errors


def run_all() -> None:
    tests = load_tests()
    passed = failed = 0

    for t in tests:
        tid = t["id"]
        question = t["question"]
        expected_type = t.get("type")
        expect = t.get("expect", {})

        actual_type = classify_query(question)
        result = query(question)

        errors = []
        if expected_type and actual_type != expected_type:
            errors.append(f"分类 {actual_type} != 期望 {expected_type}")

        if result["type"] == "retrieval":
            errors.extend(check_retrieval(result, expect))
        elif result["type"] == "statistical":
            errors.extend(check_statistical(result, expect))
        elif result["type"] == "summarization":
            errors.extend(check_summarization(result, expect))

        if errors:
            print(f"❌ {tid}: {question}")
            for e in errors:
                print(f"   - {e}")
            failed += 1
        else:
            print(f"✅ {tid}")
            passed += 1

    print(f"\n结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 条")


if __name__ == "__main__":
    run_all()
