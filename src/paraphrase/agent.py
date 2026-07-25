"""Paraphrase Agent：chunk text → rag-sentences。"""

from __future__ import annotations

from typing import Any

from src.llm import get_llm_client, get_llm_model
from src.paraphrase.models import ParaphraseResult
from src.paraphrase.prompt import load_rag_sentence_prompt
from src.rag_sentences import max_sentences_per_chunk
from src.store import load_config


def _cfg() -> dict[str, Any]:
    return load_config().get("paraphrase") or {}


def llm_role() -> str:
    return str(_cfg().get("llm_role") or "tags")


def _parse_lines(raw: str) -> list[str]:
    lines: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        # 去掉偶发编号前缀
        if len(s) > 2 and s[0].isdigit() and s[1] in ".)、.":
            s = s[2:].strip()
        elif s[:2] in {"- ", "* ", "• "}:
            s = s[2:].strip()
        if s and not s.startswith("{") and not s.startswith("```"):
            lines.append(s)
        if len(lines) >= max_sentences_per_chunk():
            break
    return lines


def paraphrase_chunk(
    chunk_id: str,
    text: str,
    *,
    date: str = "",
) -> ParaphraseResult:
    client = get_llm_client(llm_role())
    user = f"chunk_id: {chunk_id}\ndate: {date}\n\n输入文本：\n{text.strip()}"
    resp = client.chat.completions.create(
        model=get_llm_model(llm_role()),
        messages=[
            {"role": "system", "content": load_rag_sentence_prompt()},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    raw = (resp.choices[0].message.content or "").strip()
    sentences = _parse_lines(raw)
    return ParaphraseResult(chunk_id=chunk_id, sentences=sentences, raw=raw[:800])
