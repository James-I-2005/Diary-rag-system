"""日历洞察：整月词云（无 LLM）+ 日总结/诗句（轻量 Agent，本地 JSON 缓存）。"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jieba.posseg as pseg

from src.diary_calendar import get_diary_by_date
from src.llm import get_llm_client, get_llm_model
from src.store import get_db, load_config, resolve_path
from src.tokenize import is_stopword

POETIC_PROMPT = """你是一位细腻而克制的日记编辑。根据这一天的检索句（rag-sentence），写出一日印象。

要求：
1. summary：一段温暖而客观、简练的中文段落（约 80～180 字）。尽量涵盖当天日记的主要情节、人物与情绪，不堆砌形容词，不空泛抒情，不编造未出现的重大事实；允许轻度意象化提炼与顺承。
2. verse：一句合适的诗句（优先古典/近现代名句；若无贴切名句可用原创，须在 verse_source 标明「原创」）。
3. verse_source：诗句出处。名句写「作者《篇名》」；原创写「原创」。
4. verse_explain：用一两句白话解释诗句含义（点出意象即可）。
5. verse_why：说明为什么选这句——须扣住当天日记里的具体情节或情绪，勿空泛赞美。
6. 严格返回 JSON，不要 markdown 代码块，不要其他说明：
{"summary":"一段总结…","verse":"一句诗","verse_source":"作者《篇名》","verse_explain":"含义…","verse_why":"因为这一天…"}
"""

_WORDCLOUD_EXTRA_STOP = frozenset(
    {
        "就是",
        "能够",
        "感觉",
        "不是",
        "并且",
        "真的",
        "看到",
        "任何",
        "像是",
        "当时",
        "或者",
        "整个",
        "起来",
        "产生",
        "其实",
        "实际上",
        "想要",
        "还是",
        "刚刚",
        "对于",
        "当中",
        "更加",
        "说道",
        "那种",
        "这种",
        "一会儿",
        "下去",
        "上来",
        "出来",
        "进去",
        "过来",
        "过去",
        "回来",
        "回去",
        "完全",
        "比较",
        "有点",
        "有些",
        "好像",
        "似乎",
        "几乎",
        "确实",
        "当然",
        "忽然",
        "突然",
        "终于",
        "竟然",
        "居然",
        "甚至",
        "反而",
        "却是",
        "表示",
        "说明",
        "以为",
        "了解",
        "明白",
        "清楚",
        "发现",
        "出现",
        "成为",
        "作为",
        "通过",
        "关于",
        "以及",
        "同时",
        "之后",
        "之前",
        "然后",
        "现在",
        "时候",
        "地方",
        "东西",
        "事情",
        "问题",
        "方面",
        "情况",
        "这样",
        "那样",
        "如此",
        "怎么",
        "如何",
        "为何",
        "为什么",
        "多少",
        "几个",
        "一个",
        "一样",
        "一直",
        "一定",
        "可能",
        "应该",
        "需要",
        "知道",
        "觉得",
        "认为",
        "感到",
        "进行",
        "开始",
        "结束",
        "可以",
        "没有",
        "什么",
        "这个",
        "那个",
        "我们",
        "他们",
        "她们",
        "自己",
        "因为",
        "所以",
        "但是",
        "如果",
        "虽然",
        "已经",
        "还是",
        "而且",
        "不过",
        "只是",
        "只有",
        "只要",
        "今天",
        "明天",
        "昨天",
        "晚上",
        "早上",
        "下午",
        "上午",
        "中午",
        "一天",
        "有人",
        "别人",
        "大家",
        "个人",
        "人们",
        "一下",
        "一些",
        "一种",
        "一部分",
        "无法",
        "不能",
        "不会",
        "不要",
        "不必",
        "必须",
        "应当",
        "希望",
        "想到",
        "听见",
        "说道",
        "告诉",
        "问问",
        "看看",
        "想想",
    }
)

_CONTENT_POS_PREFIX = frozenset({"n", "v", "a"})
_CONTENT_POS_EXACT = frozenset({"i", "l", "j", "vn", "an", "nz", "nr", "ns", "nt", "nrt"})


def _cfg() -> dict[str, Any]:
    return load_config().get("day_insights") or {}


def llm_role() -> str:
    return str(_cfg().get("llm_role") or "tags")


def wordcloud_top_n() -> int:
    return int(_cfg().get("wordcloud_top_n") or 60)


def month_wordcloud_top_n() -> int:
    return int(_cfg().get("month_wordcloud_top_n") or _cfg().get("wordcloud_top_n") or 80)


def cache_dir() -> Path:
    rel = str(_cfg().get("cache_dir") or "data/day_insights")
    path = resolve_path(rel)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path_for_date(date: str) -> Path:
    return cache_dir() / f"{date}.json"


def wordcloud_cache_path(year_month: str) -> Path:
    return cache_dir() / f"wordcloud_{year_month}.json"


def month_content_fingerprint(year: int, month: int) -> str:
    """用 chunk 条数 + 正文字符总量作指纹，入库变化后自动失效缓存。"""
    prefix = f"{year:04d}-{month:02d}-"
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(text)), 0) AS chars
            FROM chunks WHERE date LIKE ?
            """,
            (prefix + "%",),
        ).fetchone()
    finally:
        conn.close()
    return f"{int(row['n'])}:{int(row['chars'])}"


def _is_wordcloud_stop(token: str) -> bool:
    t = (token or "").strip()
    if not t or len(t) < 2:
        return True
    if is_stopword(t):
        return True
    if t in _WORDCLOUD_EXTRA_STOP:
        return True
    if t.isdigit():
        return True
    return False


def _is_content_pos(flag: str) -> bool:
    f = (flag or "").strip()
    if not f:
        return False
    if f in _CONTENT_POS_EXACT:
        return True
    return f[0] in _CONTENT_POS_PREFIX


def content_token_counts(text: str) -> Counter[str]:
    """词云专用：停用词 + 额外虚词 + 词性过滤后的词频。"""
    counts: Counter[str] = Counter()
    if not (text or "").strip():
        return counts
    for word, flag in pseg.cut(text):
        t = (word or "").strip()
        if _is_wordcloud_stop(t):
            continue
        if not _is_content_pos(flag):
            continue
        counts[t] += 1
    return counts


def _words_from_counts(counts: Counter[str], top_n: int) -> list[dict[str, Any]]:
    items = [(w, c) for w, c in counts.items() if len(w) >= 2]
    items.sort(key=lambda x: (-x[1], x[0]))
    items = items[: max(1, top_n)] if items else []
    max_c = items[0][1] if items else 1
    return [
        {"text": w, "weight": round(c / max_c, 4), "count": int(c)}
        for w, c in items
    ]


def sentences_for_date(date: str) -> list[str]:
    s = (date or "").strip()
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT text FROM rag_sentences
            WHERE date = ?
            ORDER BY chunk_id, sent_index, id
            """,
            (s,),
        ).fetchall()
    finally:
        conn.close()
    return [str(r["text"] or "").strip() for r in rows if str(r["text"] or "").strip()]


def texts_for_month(year: int, month: int) -> tuple[str, list[str]]:
    """拼接某月全部 chunk 原文，返回 (text, dates_with_diary)。"""
    if not (1 <= month <= 12) or year < 1900:
        raise ValueError(f"非法年月: {year}-{month}")
    prefix = f"{year:04d}-{month:02d}-"
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT date, text FROM chunks
            WHERE date LIKE ?
            ORDER BY date, source_file, chunk_index, id
            """,
            (prefix + "%",),
        ).fetchall()
    finally:
        conn.close()
    dates: list[str] = []
    parts: list[str] = []
    seen: set[str] = set()
    for r in rows:
        d = str(r["date"] or "")
        t = str(r["text"] or "").strip()
        if not t:
            continue
        if d and d not in seen:
            seen.add(d)
            dates.append(d)
        parts.append(t)
    return "\n\n".join(parts), dates


def wordcloud_for_date(date: str, *, top_n: int | None = None) -> dict[str, Any]:
    diary = get_diary_by_date(date)
    text = diary.get("text") or ""
    n = top_n if top_n is not None else wordcloud_top_n()
    if not text.strip():
        return {"date": diary["date"], "words": [], "source": "chunks"}
    words = _words_from_counts(content_token_counts(text), n)
    return {
        "date": diary["date"],
        "words": words,
        "source": "chunks",
        "token_total": sum(w["count"] for w in words),
    }


def wordcloud_for_month(
    year: int,
    month: int,
    *,
    top_n: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """整月日记词云（无 LLM）；默认读本地缓存，内容指纹变化或 refresh 时重算。"""
    ym = f"{year:04d}-{month:02d}"
    n = top_n if top_n is not None else month_wordcloud_top_n()
    fp = month_content_fingerprint(year, month)
    path = wordcloud_cache_path(ym)

    if not refresh and path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if (
                isinstance(cached, dict)
                and cached.get("fingerprint") == fp
                and cached.get("top_n") == n
                and isinstance(cached.get("words"), list)
            ):
                cached["cached"] = True
                cached["month"] = ym
                return cached
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    text, dates = texts_for_month(year, month)
    if not text.strip():
        payload = {
            "month": ym,
            "words": [],
            "day_count": 0,
            "dates": [],
            "token_total": 0,
            "source": "chunks",
            "fingerprint": fp,
            "top_n": n,
            "cached": False,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    words = _words_from_counts(content_token_counts(text), n)
    payload = {
        "month": ym,
        "words": words,
        "day_count": len(dates),
        "dates": dates,
        "token_total": sum(w["count"] for w in words),
        "source": "chunks",
        "fingerprint": fp,
        "top_n": n,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "just_generated": True,
    }
    path.write_text(
        json.dumps({k: v for k, v in payload.items() if k != "just_generated"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _parse_poetic(raw: str) -> dict[str, Any]:
    data = json.loads(_strip_json_fence(raw))
    summary = str(data.get("summary") or "").strip()
    # 兼容旧字段 phrases
    if not summary:
        phrases = data.get("phrases") or []
        if isinstance(phrases, str):
            phrases = [phrases]
        summary = "；".join(str(p).strip() for p in phrases if str(p).strip())
    return {
        "summary": summary,
        "verse": str(data.get("verse") or "").strip(),
        "verse_source": str(data.get("verse_source") or "").strip(),
        "verse_explain": str(data.get("verse_explain") or "").strip(),
        "verse_why": str(data.get("verse_why") or "").strip(),
    }


def _empty_poetic(day: str, source: str, sentence_count: int = 0) -> dict[str, Any]:
    return {
        "date": day,
        "summary": "",
        "verse": "",
        "verse_source": "",
        "verse_explain": "",
        "verse_why": "",
        "source": source,
        "sentence_count": sentence_count,
        "cached": False,
    }


def load_cached_poetic(date: str) -> dict[str, Any] | None:
    path = cache_path_for_date(date)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    summary = str(data.get("summary") or "").strip()
    verse = str(data.get("verse") or "").strip()
    if not summary and not verse:
        return None
    data["date"] = date
    data["cached"] = True
    return data


def save_poetic_cache(date: str, payload: dict[str, Any]) -> Path:
    path = cache_path_for_date(date)
    to_save = {
        "date": date,
        "summary": payload.get("summary") or "",
        "verse": payload.get("verse") or "",
        "verse_source": payload.get("verse_source") or "",
        "verse_explain": payload.get("verse_explain") or "",
        "verse_why": payload.get("verse_why") or "",
        "source": payload.get("source") or "",
        "sentence_count": int(payload.get("sentence_count") or 0),
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "version": 2,
    }
    path.write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _generate_poetic(day: str) -> dict[str, Any]:
    diary = get_diary_by_date(day)
    sents = sentences_for_date(day)
    source = "rag_sentences"
    if not sents:
        source = "chunks_fallback"
        text = (diary.get("text") or "").strip()
        if not text:
            return _empty_poetic(day, source)
        sents = [text[:2400]]

    body = "\n".join(f"- {t}" for t in sents[:40])
    user = f"日期：{day}\n\n当日句子：\n{body}"

    client = get_llm_client(llm_role())
    resp = client.chat.completions.create(
        model=get_llm_model(llm_role()),
        messages=[
            {"role": "system", "content": POETIC_PROMPT},
            {"role": "user", "content": user},
        ],
        temperature=0.55,
    )
    raw = (resp.choices[0].message.content or "").strip()
    try:
        parsed = _parse_poetic(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        out = _empty_poetic(day, source, len(sents))
        out["parse_error"] = True
        out["raw"] = raw[:400]
        return out

    return {
        "date": day,
        "summary": parsed.get("summary") or "",
        "verse": parsed.get("verse") or "",
        "verse_source": parsed.get("verse_source") or "",
        "verse_explain": parsed.get("verse_explain") or "",
        "verse_why": parsed.get("verse_why") or "",
        "source": source,
        "sentence_count": len(sents),
        "cached": False,
    }


def poetic_summary_for_date(date: str, *, refresh: bool = False) -> dict[str, Any]:
    """日总结 + 诗句；默认读本地缓存，refresh=True 时强制重生成并覆盖。"""
    day = get_diary_by_date(date)["date"]
    if not refresh:
        cached = load_cached_poetic(day)
        if cached:
            return cached

    result = _generate_poetic(day)
    if result.get("summary") or result.get("verse"):
        save_poetic_cache(day, result)
        result["cached"] = False
        result["just_generated"] = True
    return result
