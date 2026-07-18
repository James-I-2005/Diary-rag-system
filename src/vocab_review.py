"""词表质量评估：规则预过滤 + 轻量 LLM 自学习停用词。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.llm import get_llm_client, get_llm_model
from src.tokenize import append_stopwords, clear_stopwords_cache
from src.vocabulary import TermRecord, VocabularyBuildResult, VocabularyConfig

# 时间点 / 钟点（九點、十點、一點…）
_TIME_POINT_RE = re.compile(
    r"^(?:[一二三四五六七八九十兩廿卅]+)?點$"
    r"|^(?:十[一二三四五六七八九]|十一|十二|[一二三四五六七八九])$"
)
# 时段词（早晨/上午类，对检索区分度低）
_TIME_PERIOD_RE = re.compile(
    r"^(?:早晨|上午|中午|下午|傍晚|晚上|夜里|夜間|午前|午後|清晨|黃昏)$"
)
# 泛称谓（无专名时不宜作关键词）
_GENERIC_TITLE_RE = re.compile(
    r"^(?:先生|小姐|太太|處長|課長|主任|經理|董事|監督)$"
)

REVIEW_PROMPT = """你是个人日记 RAG 系统的「关键词质检员」。任务：从候选词中挑出**不适合作为检索关键词**的词。

## 适合保留（有意义）
- 具体事件/活动：打牌、拜訪、結婚、開會、旅行
- 具体地点：彰化、醫院、車站（若带专名更佳）
- 人物/专名：母親、姊夫、四舅（含称谓的人名片段）
- 主题/事项：交通、房屋、工廠、結婚
- 情绪/评价（有区分度）：失望、高興

## 应剔除（无意义或噪声）
- 时间锚点：九點、十點、十一、十二、一點、兩點
- 时段泛词：早晨、上午、午餐、晚餐（若仅表时间无事件信息）
- 功能词/虚词：可以、已經、沒有、但是、還是、覺得、實在、許多、很多
- 代词/泛指：他們、我們、一起、一切、這樣、那樣
- 泛称谓（无专名）：先生、小姐、處長、課長
- 切分错误/碎片：君來訪、兄來訪、碧蓮到、半到
- 过于笼统、几乎每篇都出现的词：回家、回來、上班、下班、工作、生活

## 候选词（附 df=出现在多少篇日记, tf=总次数）
{term_lines}

请**只**返回 JSON，不要其他文字：
{{
  "meaningless": ["词1", "词2"]
}}

规则：
- meaningless 必须是上面列表中出现过的词，不要编造
- 宁可少剔、保留有检索价值的专名和事件
- 繁体中文保持原字形
"""


@dataclass
class VocabReviewConfig:
    enabled: bool = True
    llm_role: str = "tags"
    batch_size: int = 40
    rebuild_after_learn: bool = True
    append_stopwords: bool = True
    apply_rule_filters: bool = True


@dataclass
class VocabReviewResult:
    rule_rejected: list[str] = field(default_factory=list)
    llm_rejected: list[str] = field(default_factory=list)
    appended_stopwords: list[str] = field(default_factory=list)
    reviewed_at: str = ""

    @property
    def all_rejected(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for term in [*self.rule_rejected, *self.llm_rejected]:
            if term not in seen:
                seen.add(term)
                out.append(term)
        return out


def resolve_vocab_review_config() -> VocabReviewConfig:
    import os

    from src.store import load_config

    cfg = (load_config().get("vocabulary") or {}).get("review") or {}

    def _bool(name: str, default: bool) -> bool:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def _int(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        return int(raw) if raw else default

    return VocabReviewConfig(
        enabled=_bool("VOCAB_REVIEW_ENABLED", bool(cfg.get("enabled", True))),
        llm_role=os.getenv("VOCAB_REVIEW_LLM_ROLE", cfg.get("llm_role", "tags")).strip()
        or "tags",
        batch_size=_int("VOCAB_REVIEW_BATCH_SIZE", int(cfg.get("batch_size", 40))),
        rebuild_after_learn=_bool(
            "VOCAB_REVIEW_REBUILD",
            bool(cfg.get("rebuild_after_learn", True)),
        ),
        append_stopwords=_bool(
            "VOCAB_REVIEW_APPEND_STOPWORDS",
            bool(cfg.get("append_stopwords", True)),
        ),
        apply_rule_filters=_bool(
            "VOCAB_REVIEW_RULE_FILTER",
            bool(cfg.get("apply_rule_filters", True)),
        ),
    )


def is_rule_noise_term(term: str) -> bool:
    """规则层：明显的时间锚点 / 泛称谓，无需调用 LLM。"""
    if not term:
        return True
    if _TIME_POINT_RE.match(term):
        return True
    if _TIME_PERIOD_RE.match(term):
        return True
    if _GENERIC_TITLE_RE.match(term):
        return True
    return False


def rule_filter_terms(terms: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    rejected: list[str] = []
    for term in terms:
        if is_rule_noise_term(term):
            rejected.append(term)
        else:
            kept.append(term)
    return kept, rejected


def _parse_llm_json(raw: str) -> dict:
    raw = (raw or "{}").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(line for line in lines if not line.strip().startswith("```"))
    return json.loads(raw)


def _format_term_lines(records: list[TermRecord]) -> str:
    lines: list[str] = []
    for rec in records:
        lines.append(f"- {rec.term} (df={rec.df}, tf={rec.total_tf})")
    return "\n".join(lines)


def llm_review_terms(
    records: list[TermRecord],
    *,
    review_cfg: VocabReviewConfig | None = None,
) -> list[str]:
    """批量调用轻量 LLM，返回应剔除的词。"""
    review_cfg = review_cfg or resolve_vocab_review_config()
    if not records:
        return []

    client = get_llm_client(review_cfg.llm_role)
    model = get_llm_model(review_cfg.llm_role)
    meaningless: list[str] = []
    allowed = {r.term for r in records}

    for i in range(0, len(records), review_cfg.batch_size):
        batch = records[i : i + review_cfg.batch_size]
        prompt = REVIEW_PROMPT.format(term_lines=_format_term_lines(batch))
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        try:
            response = client.chat.completions.create(
                **kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            response = client.chat.completions.create(**kwargs)

        data = _parse_llm_json(response.choices[0].message.content or "{}")
        for term in data.get("meaningless") or []:
            t = str(term).strip()
            if t and t in allowed:
                meaningless.append(t)

    return list(dict.fromkeys(meaningless))


def review_vocabulary(
    result: VocabularyBuildResult,
    review_cfg: VocabReviewConfig | None = None,
) -> VocabReviewResult:
    """评估词表，将无意义词写入停用词表（自学习）。"""
    review_cfg = review_cfg or resolve_vocab_review_config()
    out = VocabReviewResult(reviewed_at=datetime.now(timezone.utc).isoformat())

    terms = list(result.terms)
    records_by_term = {r.term: r for r in result.records}

    if review_cfg.apply_rule_filters:
        _, out.rule_rejected = rule_filter_terms(terms)

    llm_candidates = [
        records_by_term[t]
        for t in terms
        if t not in out.rule_rejected and t in records_by_term
    ]

    if review_cfg.enabled and llm_candidates:
        try:
            out.llm_rejected = llm_review_terms(llm_candidates, review_cfg=review_cfg)
        except Exception as exc:
            print(f"  [warn] LLM 词表评估失败，跳过自学习: {exc}")

    if review_cfg.append_stopwords and out.all_rejected:
        comment = f"vocab_review {out.reviewed_at[:10]} auto-learned"
        out.appended_stopwords = append_stopwords(
            out.all_rejected,
            comment=comment,
            source="auto_learned",
        )
        clear_stopwords_cache()

    return out


def rebuild_vocabulary_after_review(
    cfg: VocabularyConfig,
) -> VocabularyBuildResult:
    """停用词更新后重新统计词表。"""
    from src.vocabulary import build_vocabulary_from_db

    clear_stopwords_cache()
    return build_vocabulary_from_db(cfg)
