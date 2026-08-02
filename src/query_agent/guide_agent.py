"""Guide → 预选池 → Judge 召回编排（取代打捞式 ReAct 主路径）。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.context.models import ConversationState
from src.llm import get_llm_client, get_llm_model
from src.query_agent.mentions import resolve_mentions
from src.query_agent.models import StructuredQuery
from src.query_agent.pool import build_subquestion_pool
from src.query_agent.react_agent import AgentRetrievalResult
from src.store import load_config, resolve_path

_GUIDE_PROMPT = Path(__file__).with_name("guide_prompt.md")
_JUDGE_PROMPT = Path(__file__).with_name("judge_prompt.md")

_SOURCE_PRIORITY = {"user_tag": 0, "grep": 1, "rag": 2, "unknown": 9}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cfg() -> dict[str, Any]:
    return load_config().get("query_agent") or {}


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
    raise ValueError(f"无法解析 Guide/Judge JSON: {raw[:400]!r}")


def _load_prompt(path: Path, fallback: str) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return fallback


def _llm_json(system: str, user: str, *, role: str | None = None) -> dict[str, Any]:
    cfg = _cfg()
    llm_role = role or str(cfg.get("llm_role") or "tags")
    client = get_llm_client(llm_role)
    model = get_llm_model(llm_role)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
    )
    raw = (resp.choices[0].message.content or "").strip()
    return _parse_json_object(raw)


def _primary_source(chunk: dict[str, Any]) -> str:
    sources = chunk.get("sources")
    if isinstance(sources, list) and sources:
        best = min(
            (str(s) for s in sources),
            key=lambda s: _SOURCE_PRIORITY.get(s, 5),
        )
        return best
    src = str(chunk.get("source") or "unknown")
    if "+" in src:
        parts = src.split("+")
        return min(parts, key=lambda s: _SOURCE_PRIORITY.get(s, 5))
    return src or "unknown"


def split_quota(total: int, n: int) -> list[int]:
    """均分；余数给前面的子问题。"""
    n = max(1, int(n))
    total = max(0, int(total))
    base = total // n
    rem = total % n
    return [base + (1 if i < rem else 0) for i in range(n)]


def apply_source_quota(chunks: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """按 tag > grep > rag 取满 k 条。"""
    if k <= 0 or not chunks:
        return []
    ranked = sorted(
        chunks,
        key=lambda c: (
            _SOURCE_PRIORITY.get(_primary_source(c), 5),
            -float(c.get("winning_sentence_score") or c.get("score") or 0.0),
            c.get("date") or "",
            str(c.get("chunk_id") or c.get("id") or ""),
        ),
    )
    return ranked[:k]


def aggregate_subquestion_results(
    parts: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """跨子问题去重；合并 subquestion_ids / sources。"""
    groups: dict[str, dict[str, Any]] = {}
    for lst in parts:
        for c in lst or []:
            cid = str(c.get("chunk_id") or c.get("id") or "").strip()
            if not cid:
                continue
            sq = c.get("subquestion_id")
            if cid not in groups:
                g = dict(c)
                g["id"] = cid
                g["chunk_id"] = cid
                g["subquestion_ids"] = [sq] if sq else []
                srcs = c.get("sources")
                if isinstance(srcs, list):
                    g["sources"] = list(srcs)
                else:
                    s = str(c.get("source") or "")
                    g["sources"] = [x for x in s.split("+") if x] or (
                        [s] if s else []
                    )
                groups[cid] = g
                continue
            g = groups[cid]
            if sq and sq not in g["subquestion_ids"]:
                g["subquestion_ids"].append(sq)
            extra = c.get("sources")
            if isinstance(extra, list):
                for s in extra:
                    if s and s not in g["sources"]:
                        g["sources"].append(s)
            else:
                s = str(c.get("source") or "")
                if s and s not in g["sources"]:
                    g["sources"].append(s)
            if float(c.get("score") or 0) > float(g.get("score") or 0):
                g["score"] = c.get("score")
                for key in (
                    "winning_sentence_id",
                    "winning_sentence_text",
                    "winning_sentence_score",
                    "matched_sentences",
                    "text",
                    "evidence_text",
                ):
                    if c.get(key) is not None:
                        g[key] = c.get(key)
            g["source"] = "+".join(g["sources"])
    return list(groups.values())


class GuideQueryAgent:
    """Guide → Pool → Judge 编排器。"""

    def __init__(self, context_engine: Any | None = None) -> None:
        self.context_engine = context_engine

    def k_final(self) -> int:
        return max(1, int(_cfg().get("k_final", 15)))

    def max_subquestions(self) -> int:
        return max(1, min(3, int(_cfg().get("max_subquestions", 3))))

    def retrieve(
        self,
        raw_query: str,
        *,
        state: ConversationState | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        dates: list[str] | None = None,
        scheme: str | None = None,
    ) -> AgentRetrievalResult:
        q = (raw_query or "").strip()
        timeline: list[dict[str, Any]] = []
        tool_trace: list[dict[str, Any]] = []

        def tl(event: str, **payload: Any) -> None:
            timeline.append({"t": len(timeline) + 1, "event": event, "at": _now(), **payload})

        if not q:
            sq = StructuredQuery(
                original_query="",
                rewritten_query="",
                need_retrieval=False,
                source="guide",
            )
            return AgentRetrievalResult(
                structured=sq,
                stop_reason="empty_query",
                timeline=timeline,
            )

        mentions = resolve_mentions(q)
        tl("mentions", **mentions)

        guide = self._run_guide(q, state=state, mentions=mentions)
        tl("guide", guide=guide)

        need = bool(guide.get("need_retrieval", True))
        themes_all: list[str] = []
        for sq in guide.get("subquestions") or []:
            themes_all.extend(sq.get("rag_themes") or [])
        structured = StructuredQuery(
            original_query=q,
            rewritten_query=q,
            query_sentences=themes_all[:6] or ([q] if need else []),
            need_retrieval=need,
            intent="guide",
            source="guide",
            meta={"guide": guide, "mentions": mentions},
        )
        if dates:
            structured.dates = list(dates)
        else:
            structured.date_from = (date_from or "").strip()
            structured.date_to = (date_to or "").strip()

        if not need:
            self._maybe_save_debug(q, guide, mentions, [], timeline, "no_retrieval")
            return AgentRetrievalResult(
                structured=structured,
                analysis=guide,
                timeline=timeline,
                stop_reason="no_retrieval",
            )

        subquestions = self._normalize_subquestions(guide, mentions, q)
        if not subquestions:
            subquestions = [
                {
                    "id": "sq1",
                    "text": q,
                    "grep_terms": [],
                    "rag_themes": [q],
                    "tag_ids": [t["id"] for t in mentions.get("tags") or []],
                    "tag_names": [t["name"] for t in mentions.get("tags") or []],
                }
            ]

        quotas = split_quota(self.k_final(), len(subquestions))
        tl(
            "subquestions",
            items=[{"id": s["id"], "text": s["text"], "quota": quotas[i]} for i, s in enumerate(subquestions)],
        )

        selected_parts: list[list[dict[str, Any]]] = []

        for i, sub in enumerate(subquestions):
            quota = quotas[i]
            pool = build_subquestion_pool(
                sub_text=sub["text"],
                grep_terms=sub.get("grep_terms") or [],
                rag_themes=sub.get("rag_themes") or [],
                tag_ids=sub.get("tag_ids") or [],
                date_from=date_from,
                date_to=date_to,
                dates=dates,
                scheme=scheme,
            )
            for c in pool:
                c["subquestion_id"] = sub["id"]
            relevant, judge_dbg = self._run_judge(sub["text"], pool)
            picked = apply_source_quota(relevant, quota)
            selected_parts.append(picked)
            meta = {
                "subquestion_id": sub["id"],
                "pool_size": len(pool),
                "relevant": len(relevant),
                "picked": len(picked),
                "quota": quota,
            }
            tl("subquestion_done", **meta)
            tl(
                "judge",
                subquestion_id=sub["id"],
                subquestion=sub["text"],
                input=judge_dbg.get("input"),
                output=judge_dbg.get("output"),
                error=judge_dbg.get("error"),
            )
            tool_trace.append({"tool": "subquestion_pipeline", **meta})

        final_chunks = aggregate_subquestion_results(selected_parts)
        # 全局再按优先级截到 K_final（去重后可能仍略多）
        final_chunks = apply_source_quota(final_chunks, self.k_final())
        tl("final", n_chunks=len(final_chunks))

        self._maybe_save_debug(
            q, guide, mentions, final_chunks, timeline, "ok"
        )
        return AgentRetrievalResult(
            structured=structured,
            chunks=final_chunks,
            analysis=guide,
            timeline=timeline,
            tool_trace=tool_trace,
            stop_reason="ok",
        )

    def _normalize_subquestions(
        self,
        guide: dict[str, Any],
        mentions: dict[str, Any],
        fallback_q: str,
    ) -> list[dict[str, Any]]:
        raw = list(guide.get("subquestions") or [])[: self.max_subquestions()]
        tag_by_name = {
            t["name"]: t["id"] for t in (mentions.get("tags") or [])
        }
        all_tag_ids = [t["id"] for t in (mentions.get("tags") or [])]
        out: list[dict[str, Any]] = []
        for i, sq in enumerate(raw):
            text = str(sq.get("text") or fallback_q).strip() or fallback_q
            grep_terms = [
                str(t).strip()
                for t in (sq.get("grep_terms") or [])
                if str(t).strip()
            ]
            rag_themes = [
                str(t).strip()
                for t in (sq.get("rag_themes") or [])
                if str(t).strip()
            ][:3]
            names = [
                str(n).strip()
                for n in (sq.get("tag_names") or [])
                if str(n).strip()
            ]
            tag_ids = [tag_by_name[n] for n in names if n in tag_by_name]
            # 若 Guide 未写 tag_names 但全文有 @，把全部 tag 挂到每个子问题
            # （多子问题时均分在 pool_tag 内按 tag 做）
            if not tag_ids and all_tag_ids:
                tag_ids = list(all_tag_ids)
            if not rag_themes and not grep_terms:
                rag_themes = [text]
            out.append(
                {
                    "id": str(sq.get("id") or f"sq{i+1}"),
                    "text": text,
                    "grep_terms": grep_terms,
                    "rag_themes": rag_themes,
                    "tag_ids": tag_ids,
                    "tag_names": names,
                }
            )
        return out

    def _run_guide(
        self,
        query: str,
        *,
        state: ConversationState | None,
        mentions: dict[str, Any],
    ) -> dict[str, Any]:
        system = _load_prompt(
            _GUIDE_PROMPT,
            '输出 JSON：{"need_retrieval":true,"analysis":"","subquestions":[]}',
        )
        parts = [f"【当前用户问题】\n{query}"]
        if mentions.get("mention_names"):
            parts.append(
                "【已解析的 @mention】\n"
                + json.dumps(mentions, ensure_ascii=False)
            )
        if state and self.context_engine is not None:
            try:
                if state.summary:
                    parts.insert(0, f"【对话摘要】\n{state.summary}")
                recent_msgs = (state.messages or [])[-6:]
                if recent_msgs:
                    lines = []
                    for m in recent_msgs:
                        lines.append(f"{m.role}: {(m.content or '')[:300]}")
                    parts.insert(
                        0 if not state.summary else 1,
                        "【最近对话】\n" + "\n".join(lines),
                    )
            except Exception:
                pass
        user = "\n\n".join(parts)
        try:
            data = _llm_json(system, user)
        except Exception as exc:
            return {
                "need_retrieval": True,
                "analysis": f"guide_fallback: {exc}",
                "subquestions": [
                    {
                        "id": "sq1",
                        "text": query,
                        "grep_terms": [],
                        "rag_themes": [query],
                        "tag_names": list(mentions.get("mention_names") or []),
                    }
                ],
            }
        data.setdefault("need_retrieval", True)
        data.setdefault("subquestions", [])
        return data

    def _run_judge(
        self, sub_text: str, pool: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not pool:
            return [], {
                "input": {"subquestion": sub_text, "candidates": []},
                "output": {"relevant_ids": []},
            }
        system = _load_prompt(
            _JUDGE_PROMPT,
            '输出 JSON：{"relevant_ids":[]}',
        )
        cands = []
        for c in pool:
            cid = str(c.get("chunk_id") or c.get("id") or "")
            sent = (c.get("winning_sentence_text") or "").strip()
            if not sent:
                text = (c.get("text") or "")[:180]
                sent = text
            cands.append(
                {
                    "id": cid,
                    "date": c.get("date") or "",
                    "source": c.get("source") or _primary_source(c),
                    "sentence": sent[:400],
                }
            )
        judge_input = {"subquestion": sub_text, "candidates": cands}
        user = json.dumps(judge_input, ensure_ascii=False)
        try:
            data = _llm_json(system, user)
            ids = {
                str(x).strip()
                for x in (data.get("relevant_ids") or [])
                if str(x).strip()
            }
            dbg = {"input": judge_input, "output": data}
        except Exception as exc:
            dbg = {
                "input": judge_input,
                "output": None,
                "error": str(exc),
            }
            # Judge 失败：保守保留全池，交给配额截断
            return list(pool), dbg
        if not ids:
            return [], dbg
        relevant = [
            c
            for c in pool
            if str(c.get("chunk_id") or c.get("id") or "") in ids
        ]
        return relevant, dbg

    def _maybe_save_debug(
        self,
        query: str,
        guide: dict[str, Any],
        mentions: dict[str, Any],
        chunks: list[dict[str, Any]],
        timeline: list[dict[str, Any]],
        stop_reason: str,
    ) -> None:
        cfg = _cfg()
        if not cfg.get("save_debug_json", True):
            return
        path = resolve_path(str(cfg.get("debug_path") or "data/last_query.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": "guide",
            "at": _now(),
            "query": query,
            "stop_reason": stop_reason,
            "mentions": mentions,
            "guide": guide,
            "n_chunks": len(chunks),
            "chunks": [
                {
                    "id": c.get("chunk_id") or c.get("id"),
                    "date": c.get("date"),
                    "source": c.get("source"),
                    "sources": c.get("sources"),
                    "winning_sentence_text": (c.get("winning_sentence_text") or "")[
                        :200
                    ],
                    "subquestion_ids": c.get("subquestion_ids"),
                }
                for c in chunks
            ],
            "timeline": timeline,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
