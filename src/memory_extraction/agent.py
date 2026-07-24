"""Memory Extraction Agent 实现。"""

from __future__ import annotations

import json
import re
from typing import Any

from src.llm import get_llm_client, get_llm_model
from src.memory_extraction.models import ExtractionResult, MemoryViewDraft
from src.memory_extraction.prompt import EXTRACTION_PROMPT
from src.memory_views import max_views_per_chunk, normalize_view_type
from src.store import load_config


def _cfg() -> dict[str, Any]:
    return load_config().get("memory_views") or {}


def llm_role() -> str:
    ext = _cfg().get("extraction") or {}
    return str(ext.get("llm_role") or "tags")


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        data = json.loads(m.group())
        if isinstance(data, dict):
            return data
    raise ValueError(f"无法解析 Extraction JSON: {text[:200]}")


def extract_views_for_chunk(
    chunk_id: str,
    text: str,
    *,
    date: str = "",
) -> ExtractionResult:
    """对单 chunk 调用 LLM 提取 Memory Views。"""
    client = get_llm_client(llm_role())
    user_content = f"chunk_id: {chunk_id}\ndate: {date}\n\n日记片段：\n{text.strip()}"

    resp = client.chat.completions.create(
        model=get_llm_model(llm_role()),
        messages=[
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )
    raw = (resp.choices[0].message.content or "").strip()
    data = _parse_json(raw)

    views: list[MemoryViewDraft] = []
    for item in data.get("views") or []:
        if not isinstance(item, dict):
            continue
        vtype = normalize_view_type(str(item.get("type") or ""))
        content = str(item.get("content") or "").strip()
        if vtype and content:
            views.append(MemoryViewDraft(type=vtype, content=content))
        if len(views) >= max_views_per_chunk():
            break

    return ExtractionResult(chunk_id=chunk_id, views=views, raw=raw[:500])
