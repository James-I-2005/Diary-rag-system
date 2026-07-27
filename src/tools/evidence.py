"""多路召回证据合并。"""

from __future__ import annotations

from typing import Any


def merge_chunk_evidence(
    *chunk_lists: list[dict[str, Any]],
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """按 chunk_id 合并；score 取 max；sources / matched_* 并集。"""
    groups: dict[str, dict[str, Any]] = {}
    for lst in chunk_lists:
        for c in lst or []:
            cid = str(c.get("chunk_id") or c.get("id") or "").strip()
            if not cid:
                continue
            src = str(c.get("source") or "unknown")
            if cid not in groups:
                g = dict(c)
                g["id"] = cid
                g["chunk_id"] = cid
                g["unit_id"] = cid
                g["sources"] = [src] if src else []
                g["score"] = float(c.get("score") or 0.0)
                groups[cid] = g
                continue
            g = groups[cid]
            g["score"] = max(float(g.get("score") or 0.0), float(c.get("score") or 0.0))
            if src and src not in g["sources"]:
                g["sources"].append(src)
            # 保留更长正文
            if len(c.get("text") or "") > len(g.get("text") or ""):
                g["text"] = c.get("text") or ""
                g["evidence_text"] = g["text"]
            ms = c.get("matched_sentences") or []
            if ms:
                existing = g.setdefault("matched_sentences", [])
                seen = {x.get("id") for x in existing}
                for h in ms:
                    if h.get("id") not in seen:
                        existing.append(h)
            terms = c.get("matched_terms") or []
            if terms:
                et = g.setdefault("matched_terms", [])
                for t in terms:
                    if t not in et:
                        et.append(t)
            # 展示用 source：多源时拼起来
            g["source"] = "+".join(g["sources"])

    ranked = sorted(
        groups.values(),
        key=lambda g: (-float(g.get("score") or 0.0), g.get("date") or "", g["id"]),
    )
    return ranked[: max(1, int(top_k))]
