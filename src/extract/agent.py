"""Extract Agent：根据目录树（+弱线索）推断每文件日期。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.extract.dates import is_valid_date
from src.extract.models import FileNode
from src.extract.scan import peek_file
from src.llm import get_llm_client, get_llm_model
from src.store import load_config

_PROMPT_PATH = Path(__file__).with_name("prompt_extract.md")

_FALLBACK_PROMPT = """你是日记目录的日期解析助手。根据目录树推断每个文件的 YYYY-MM-DD。
只输出 JSON：{"resolved":[{"path","date","reason"}],"unresolved":["path",...]}。
推断不出则进 unresolved，禁止编造 path。"""


def load_extract_prompt() -> str:
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _FALLBACK_PROMPT.strip()


def _extract_cfg() -> dict[str, Any]:
    return load_config().get("extract") or {}


def _llm_role() -> str:
    return str(_extract_cfg().get("llm_role") or "tags")


def _peek_chars() -> int:
    return max(0, int(_extract_cfg().get("peek_chars", 200)))


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
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
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            return data
    raise ValueError(f"Extract Agent 无法解析 JSON: {raw[:300]!r}")


def build_user_payload(
    nodes: list[FileNode],
    *,
    peek_chars: int | None = None,
) -> str:
    n = _peek_chars() if peek_chars is None else peek_chars
    lines = ["## 目录树（相对路径）", *[f"- {node.path}" for node in nodes]]
    if n > 0:
        lines.append("")
        lines.append(f"## 文件开头摘录（各前 {n} 字，可为空）")
        for node in nodes:
            peek = peek_file(node.abs_path, n).replace("\n", " ").strip()
            if peek:
                lines.append(f"- {node.path}: {peek}")
    lines.append("")
    lines.append("请输出 JSON。")
    return "\n".join(lines)


def normalize_agent_result(
    data: dict[str, Any],
    known_paths: set[str],
) -> tuple[dict[str, str], list[str]]:
    """
    校验 Agent JSON。
    返回 (path→date, unresolved_paths)。
    非法 date / 未知 path 丢进 unresolved。
    """
    resolved: dict[str, str] = {}
    unresolved: list[str] = []

    for item in data.get("resolved") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().replace("\\", "/")
        date = str(item.get("date") or "").strip()
        if path not in known_paths:
            continue
        if not is_valid_date(date):
            if path not in unresolved:
                unresolved.append(path)
            continue
        resolved[path] = date

    for path in data.get("unresolved") or []:
        p = str(path or "").strip().replace("\\", "/")
        if p in known_paths and p not in resolved and p not in unresolved:
            unresolved.append(p)

    # 输入中未出现在 resolved/unresolved 的，一律 unresolved
    for p in sorted(known_paths):
        if p not in resolved and p not in unresolved:
            unresolved.append(p)

    return resolved, unresolved


class ExtractAgent:
    """目录树 → 每文件日期或 unresolved。"""

    def resolve_dates(
        self,
        nodes: list[FileNode],
    ) -> tuple[dict[str, str], list[str]]:
        if not nodes:
            return {}, []

        role = _llm_role()
        client = get_llm_client(role)
        model = get_llm_model(role)
        system = load_extract_prompt()
        user = build_user_payload(nodes)

        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json_object(raw)
        known = {n.path for n in nodes}
        return normalize_agent_result(data, known)
