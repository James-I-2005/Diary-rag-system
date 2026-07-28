"""Extract Agent：轻量路径日期推断（标准正则搞不定的目录名，如「八月」）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.extract.dates import is_valid_date
from src.extract.models import FileNode
from src.llm import get_llm_client, get_llm_model
from src.store import load_config

_PROMPT_PATH = Path(__file__).with_name("prompt_extract.md")

_FALLBACK_PROMPT = """你是日记路径/文件名日期解析助手。根据 path 与 filename 推断 YYYY-MM-DD。
只输出 JSON：{"resolved":[{"path","date","reason"}],"unresolved":["path",...]}。
推断不出则 unresolved（unknown），禁止编造。"""


def load_extract_prompt() -> str:
    if _PROMPT_PATH.is_file():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _FALLBACK_PROMPT.strip()


def _extract_cfg() -> dict[str, Any]:
    return load_config().get("extract") or {}


def _llm_role() -> str:
    return str(_extract_cfg().get("llm_role") or "tags")


def _batch_size() -> int:
    """单次发给 Agent 的路径数上限，保持轻量。"""
    return max(1, min(80, int(_extract_cfg().get("agent_batch_size", 40))))


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


def build_user_payload(nodes: list[FileNode]) -> str:
    """传相对路径 + 文件名（轻量）；不读正文。"""
    lines = [
        "## 文件列表（请据此推断日期；标准数字正则已失败）",
        "每项含 path（相对路径）与 filename（文件名，含扩展名）。",
    ]
    for node in nodes:
        filename = Path(node.path).name
        lines.append(f"- path: {node.path}")
        lines.append(f"  filename: {filename}")
    lines.extend(
        [
            "",
            "示例难例：",
            "- path=`2026/八月/31/note.md` filename=`note.md` → `2026-08-31`",
            "- path=`日记/2026年7月13日_随记.docx` filename=`2026年7月13日_随记.docx` → `2026-07-13`",
            "推断不出请放入 unresolved（视为 unknown）。",
            "请输出 JSON（resolved 里仍用 path 标识文件）。",
        ]
    )
    return "\n".join(lines)


def normalize_agent_result(
    data: dict[str, Any],
    known_paths: set[str],
) -> tuple[dict[str, str], list[str]]:
    """
    校验 Agent JSON。
    返回 (path→date, unresolved_paths)。
    非法 date / unknown / 未知 path → unresolved。
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
        # 显式 unknown
        if date.lower() in {"", "unknown", "null", "none", "?"}:
            if path not in unresolved:
                unresolved.append(path)
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

    for p in sorted(known_paths):
        if p not in resolved and p not in unresolved:
            unresolved.append(p)

    return resolved, unresolved


class ExtractAgent:
    """路径 → 每文件 date 或 unknown（unresolved）。不读正文。"""

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
        batch = _batch_size()

        all_resolved: dict[str, str] = {}
        all_unresolved: list[str] = []

        for i in range(0, len(nodes), batch):
            chunk = nodes[i : i + batch]
            user = build_user_payload(chunk)
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
            known = {n.path for n in chunk}
            resolved, unresolved = normalize_agent_result(data, known)
            all_resolved.update(resolved)
            for p in unresolved:
                if p not in all_unresolved and p not in all_resolved:
                    all_unresolved.append(p)

        return all_resolved, all_unresolved
