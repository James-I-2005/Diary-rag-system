"""回答生成：结构化结果 → 自然语言。"""

from __future__ import annotations

from src.llm import get_llm_client, get_llm_model
from src.query import query

RETRIEVAL_ANSWER_PROMPT = """你是个人日记分析助手。根据以下日记片段回答用户问题。

要求：
1. 仅基于提供的片段回答，不要编造
2. 每条信息标注来源日期，格式 [YYYY-MM-DD]
3. 简洁有条理，可用列表
4. 若片段不足以回答，明确说明

日记片段：
{context}

用户问题：{question}

回答："""

STATISTICAL_ANSWER_PROMPT = """你是个人日记分析助手。根据以下统计数据回答用户问题。

要求：
1. 统计数字必须使用提供的 count，不要修改
2. 列举具体事件时标注日期 [YYYY-MM-DD]
3. 语气自然，像朋友在帮你回顾

统计结果：
- 指标：{metric}
- 数量：{count}
- 明细：
{details}

用户问题：{question}

回答："""

SUMMARIZATION_ANSWER_PROMPT = """你是个人日记分析助手。根据以下分析数据归纳用户偏好。

要求：
1. 基于活动频次数据和日记佐证片段归纳
2. 说明「最喜欢」的依据（出现次数 + 具体经历）
3. 标注关键日期 [YYYY-MM-DD]
4. 语气温暖、个人化

活动频次排名：
{top_activities}

相关日记片段：
{evidence}

用户问题：{question}

回答："""


def format_chunks_context(chunks: list[dict], max_chars: int = 6000) -> str:
    """把 chunk 列表格式化为 prompt context，控制总长度。"""
    lines = []
    total = 0
    for c in chunks:
        line = f"[{c['date']}] {c['text']}"
        if total + len(line) > max_chars:
            lines.append("... (更多内容已省略)")
            break
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)


def format_statistical_details(details: list[dict], limit: int = 10) -> str:
    lines = []
    for d in details[:limit]:
        summary = d.get("touching_summary") or d.get("text", "")[:80]
        lines.append(f"- [{d['date']}] {summary}")
    if len(details) > limit:
        lines.append(f"... 共 {len(details)} 条，仅显示前 {limit} 条")
    return "\n".join(lines)


def _build_prompt(question: str, result: dict) -> str | None:
    if result["type"] == "retrieval":
        context = format_chunks_context(result["chunks"])
        if not context.strip():
            return None
        return RETRIEVAL_ANSWER_PROMPT.format(context=context, question=question)

    if result["type"] == "statistical":
        if result.get("error"):
            return None
        details = format_statistical_details(result.get("details", []))
        return STATISTICAL_ANSWER_PROMPT.format(
            metric=result.get("metric", ""),
            count=result["count"],
            details=details,
            question=question,
        )

    if result["type"] == "summarization":
        top_str = "\n".join(
            f"- {act}: {cnt} 次" for act, cnt in result.get("top_activities", [])
        )
        evidence_lines = []
        for act, chunks in result.get("evidence", {}).items():
            for c in chunks[:2]:
                evidence_lines.append(f"[{c['date']}] ({act}) {c['text'][:100]}")
        return SUMMARIZATION_ANSWER_PROMPT.format(
            top_activities=top_str or "（暂无活动统计）",
            evidence="\n".join(evidence_lines) or "（暂无佐证片段）",
            question=question,
        )

    return None


def fallback_answer(result: dict) -> str:
    if result["type"] == "statistical":
        if result.get("error"):
            return f"暂时无法统计：{result['error']}"
        return f"统计：{result.get('metric')} = {result.get('count')} 次"
    if result["type"] == "retrieval":
        chunks = result.get("chunks", [])
        if not chunks:
            return "未找到相关日记内容，请尝试换个问法。"
        lines = [f"[{c['date']}] {c['text'][:100]}..." for c in chunks[:5]]
        return "相关片段：\n" + "\n".join(lines)
    if result["type"] == "summarization":
        top = result.get("top_activities", [])
        if not top:
            return "暂无足够标签数据做归纳，请先运行 tags。"
        lines = [f"- {act}: {cnt} 次" for act, cnt in top[:5]]
        return "活动频次：\n" + "\n".join(lines)
    return str(result)


def generate_answer(question: str) -> str:
    result = query(question)

    if result["type"] == "retrieval" and not result.get("chunks"):
        return "未找到相关日记内容，请尝试换个问法。"
    if result["type"] == "statistical" and result.get("error"):
        return f"暂时无法统计：{result['error']}"

    prompt = _build_prompt(question, result)
    if prompt is None:
        return fallback_answer(result)

    client = get_llm_client("answer")
    try:
        response = client.chat.completions.create(
            model=get_llm_model("answer"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content or fallback_answer(result)
    except Exception as e:
        print(f"LLM 不可用，使用降级回答: {e}")
        return fallback_answer(result)


def generate_answer_stream(question: str):
    result = query(question)
    prompt = _build_prompt(question, result)
    if prompt is None:
        print(fallback_answer(result))
        return

    client = get_llm_client("answer")
    stream = client.chat.completions.create(
        model=get_llm_model("answer"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            print(delta, end="", flush=True)
    print()
