"""jieba 分词 + 实体抽取（人名 / 地名 / 机构），供 v0.1 统计标签与召回使用。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import jieba
import jieba.posseg as pseg

from src.store import load_config, resolve_path

# jieba 词性 → 实体类型
_POS_PEOPLE = frozenset({"nr", "nrt", "nrfg"})
_POS_PLACES = frozenset({"ns", "nsf"})
_POS_ORGS = frozenset({"nt", "nz"})

# 未被子词标注捕获的中文称谓（楊小姐、橋本君、沙處長…）
# 专名尽量短（1～3 字 + 称谓），减少「晚上藤田君」整段命中
_PERSON_SUFFIX_RE = re.compile(
    r"[\u4e00-\u9fff]{1,3}(?:君|氏|兄|嫂|小姐|太太|處長|課長|縣長|局長|主任|秘書|"
    r"總理|董事長|經理|監督官|書記官|理事)"
)
# 西洋人名 / Jenny 等
_LATIN_NAME_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9'.-]{1,30}\b")
# 带数字的门牌地名片段（耀華里73號）
_PLACE_ADDR_RE = re.compile(r"[\u4e00-\u9fff]{1,6}\d+號?")

_PUNCT = re.compile(
    r"[\s\.,，。！？!?:;；、\"'“”‘’（）()\[\]【】<>《》—\-·…]+"
)


@dataclass
class TextAnalysis:
    """单段文本的分词与实体分析结果。"""

    tokens: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)

    @property
    def entities(self) -> list[str]:
        """人物 + 地名 + 机构，去重保序（出现即收录，不做 TF-IDF 截断）。"""
        return _unique_keep_order([*self.people, *self.places, *self.orgs])


def _unique_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _prune_subsumed(terms: list[str]) -> list[str]:
    """去掉被更长实体包含的短片段（如保留「張小姐」、去掉「小姐」）。"""
    terms = _unique_keep_order(terms)
    kept: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        if any(term != k and term in k for k in kept):
            continue
        kept.append(term)
    return sorted(kept, key=lambda t: terms.index(t))


def _normalize_token(token: str) -> str:
    return _PUNCT.sub("", token).strip()


def get_stopwords_path() -> Path:
    cfg = load_config().get("tokenize") or {}
    rel = cfg.get("stopwords_file", "data/stopwords_zh.txt")
    return resolve_path(rel)


@lru_cache(maxsize=1)
def _load_stopwords() -> frozenset[str]:
    """合并：文件 + lexicon DB（DB 增补优先并入）。"""
    words: set[str] = set()
    path = get_stopwords_path()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            w = line.strip()
            if w and not w.startswith("#"):
                words.add(w)
    try:
        from src.lexicon_db import load_stopwords_from_db

        words.update(load_stopwords_from_db())
    except Exception:
        pass
    return frozenset(words)


def clear_stopwords_cache() -> None:
    """停用词文件/库更新后调用，使分词重新加载。"""
    _load_stopwords.cache_clear()


def append_stopwords(
    terms: Iterable[str],
    *,
    comment: str = "",
    source: str = "auto_learned",
    persist_db: bool = True,
) -> list[str]:
    """将新词追加到停用词 txt + lexicon DB（去重），返回实际新写入的词。"""
    path = get_stopwords_path()
    existing = set(_load_stopwords())
    new_terms = [t.strip() for t in terms if t and t.strip() and t.strip() not in existing]
    if not new_terms:
        return []

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        if comment:
            f.write(f"\n# {comment}\n")
        for term in new_terms:
            f.write(f"{term}\n")

    if persist_db:
        try:
            from src.lexicon_db import upsert_stopwords

            upsert_stopwords(new_terms, source=source, comment=comment)
        except Exception as exc:
            print(f"  [warn] 停用词写入 DB 失败（文件已更新）: {exc}")

    clear_stopwords_cache()
    return new_terms


def is_stopword(token: str) -> bool:
    t = _normalize_token(token)
    if not t:
        return True
    if len(t) == 1 and "\u4e00" <= t <= "\u9fff":
        return True
    return t in _load_stopwords()


def tokenize(
    text: str,
    *,
    remove_stopwords: bool = True,
    unique: bool = True,
) -> list[str]:
    """jieba 精确模式分词，可选去停用词与标点。

    unique=True（默认）：去重保序，适合做集合交集。
    unique=False：保留重复，适合统计 TF。
    """
    if not text or not text.strip():
        return []

    raw = [w for w in jieba.lcut(text) if w.strip()]
    tokens: list[str] = []
    for w in raw:
        t = _normalize_token(w)
        if not t:
            continue
        if remove_stopwords and is_stopword(t):
            continue
        tokens.append(t)
    return _unique_keep_order(tokens) if unique else tokens


def token_counts(text: str, *, remove_stopwords: bool = True) -> Counter[str]:
    """分词并统计词频（不去重）。"""
    return Counter(tokenize(text, remove_stopwords=remove_stopwords, unique=False))


def _collect_pos_entities(text: str) -> tuple[list[str], list[str], list[str]]:
    people: list[str] = []
    places: list[str] = []
    orgs: list[str] = []

    for word, flag in pseg.cut(text):
        term = _normalize_token(word)
        if not term or len(term) < 2:
            continue
        if flag in _POS_PEOPLE:
            people.append(term)
        elif flag in _POS_PLACES:
            places.append(term)
        elif flag in _POS_ORGS:
            orgs.append(term)

    return people, places, orgs


def _collect_pattern_entities(text: str) -> tuple[list[str], list[str]]:
    people = [m.group(0) for m in _PERSON_SUFFIX_RE.finditer(text)]
    people.extend(m.group(0) for m in _LATIN_NAME_RE.finditer(text))
    places = [m.group(0) for m in _PLACE_ADDR_RE.finditer(text)]
    return people, places


def extract_entities(text: str) -> dict[str, list[str]]:
    """
    从文本抽取实体；出现即收录，不受词频限制。

    返回 {"people": [...], "places": [...], "orgs": [...]}
    """
    if not text or not text.strip():
        return {"people": [], "places": [], "orgs": []}

    people, places, orgs = _collect_pos_entities(text)
    pat_people, pat_places = _collect_pattern_entities(text)
    people.extend(pat_people)
    places.extend(pat_places)

    return {
        "people": _prune_subsumed(people),
        "places": _prune_subsumed(places),
        "orgs": _prune_subsumed(orgs),
    }


def analyze_text(text: str, *, remove_stopwords: bool = True) -> TextAnalysis:
    """分词 + 实体抽取，供 chunk / question 两侧共用同一套逻辑。"""
    entities = extract_entities(text)
    return TextAnalysis(
        tokens=tokenize(text, remove_stopwords=remove_stopwords),
        people=entities["people"],
        places=entities["places"],
        orgs=entities["orgs"],
    )


def analyze_question(question: str) -> TextAnalysis:
    """用户问题：实体优先保留（即使像停用词也应匹配 Jenny 等人名）。"""
    entities = extract_entities(question)
    # 问题侧：tokens = 分词结果 ∪ 实体，便于与 chunk 侧求交
    base_tokens = tokenize(question, remove_stopwords=True)
    entity_tokens = entities["people"] + entities["places"] + entities["orgs"]
    merged = _unique_keep_order([*entity_tokens, *base_tokens])
    return TextAnalysis(
        tokens=merged,
        people=entities["people"],
        places=entities["places"],
        orgs=entities["orgs"],
    )


if __name__ == "__main__":
    samples = [
        "我和 Jenny 在滨江散步，聊了很久，风很大。",
        "與張小姐一同賞月，前往天津，與開灤白川氏會面。",
        "晚上把橋本君叫來辦公處，同沙處長、王課長討論水泥定價。",
    ]
    for s in samples:
        a = analyze_text(s)
        print("---")
        print("原文:", s)
        print("tokens:", a.tokens[:15], "..." if len(a.tokens) > 15 else "")
        print("people:", a.people)
        print("places:", a.places)
        print("orgs:", a.orgs)
        print("entities:", a.entities)
