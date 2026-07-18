"""实体清洗：规则去粘连 + 轻量 LLM 校正（人名混入「晚上/被/靠」等）。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.entities import ChunkEntityItem, ChunkEntityRow, EntityType
from src.llm import get_llm_client, get_llm_model

# 人名常见粘连前缀（动词/介词/时段/机构碎片…）
# 注意：多字前缀必须写在单字之前，否则「晚」会吃掉「晚上」的「晚」
_PERSON_PREFIX_RE = re.compile(
    r"^(?:"
    r"晚上|早晨|上午|中午|下午|傍晚|夜里|夜間|"
    r"公園|飯後|事與|之後|之前|然後|一同|一起|"
    r"華北|氮肥|肥料|氮|公司|銀行|機械|"
    r"被|靠|與|和|同|請|叫|令|帶|陪|見|訪|找|給|向|對|在|到|從|於|的|等|事|"
    r"晚|早|今|昨|前|後|又|再|就|還|也|很|不|沒|把|讓|使|用"
    r")+"
)

# 称谓后缀：清洗后应保留「专名+称谓」
_PERSON_SUFFIX = (
    "君|氏|兄|嫂|小姐|太太|處長|課長|縣長|局長|主任|秘書|"
    "總理|董事長|經理|監督官|書記官|理事"
)
# 截取时专名最多 3 字，避免「肥料土山課長」→「料土山課長」
_PERSON_TAIL_RE = re.compile(
    rf"([\u4e00-\u9fff]{{1,3}}(?:{_PERSON_SUFFIX}))$"
)

# 明显不像实体的碎片
_DROP_PERSON_RE = re.compile(
    r"^(?:先生|小姐|太太|處長|課長|主任|經理|他們|我們|大家)$"
)

CLEAN_PROMPT = """你是日记 RAG 的「实体清洗员」。候选实体来自自动抽取，常把前后字粘进专名。

## 任务
对每个人名/地名/机构给出处理：
- rewrite：改成干净专名（去掉「晚上/被/靠/公園與」等粘连）
- drop：不是实体或无法修复
- keep：已经干净，保持原样

## 示例
- 晚上藤田君 → 藤田君
- 靠賴天穎君 → 賴天穎君
- 被毛昭江君 → 毛昭江君
- 公園與沙兄 → 沙兄
- 事與金川君 → 金川君
- 肥料土山課長 → 土山課長（或 土山，若无法确定职称归属）
- 付潮 → 若不是明确人名则 drop

## 候选（格式 name|type）
{lines}

请**只**返回 JSON：
{{
  "ops": [
    {{"from": "靠賴天穎君", "to": "賴天穎君", "type": "person", "action": "rewrite"}},
    {{"from": "付潮", "to": null, "type": "person", "action": "drop"}}
  ]
}}

规则：
- from 必须来自候选列表；to 为清洗后的专名（drop 时 to=null）
- 不要编造语料中不存在的新人物
- 繁体字形保持；拉丁名如 Jenny 保留
- 宁可 keep 可疑专名，也不要乱 drop 真名
"""


@dataclass
class EntityReviewConfig:
    enabled: bool = True
    llm_role: str = "tags"
    batch_size: int = 40
    apply_rule_filters: bool = True


@dataclass
class EntityCleanOp:
    original: str
    cleaned: str | None
    entity_type: EntityType
    action: str  # keep | rewrite | drop
    source: str  # rule | llm


@dataclass
class EntityReviewResult:
    ops: list[EntityCleanOp] = field(default_factory=list)
    reviewed_at: str = ""

    @property
    def mapping(self) -> dict[tuple[str, str], tuple[str | None, str]]:
        """(name, type) → (cleaned_or_None, action)"""
        out: dict[tuple[str, str], tuple[str | None, str]] = {}
        for op in self.ops:
            out[(op.original, op.entity_type)] = (op.cleaned, op.action)
        return out


def resolve_entity_review_config() -> EntityReviewConfig:
    from src.store import load_config

    cfg = (load_config().get("vocabulary") or {}).get("entity_review") or {}

    def _bool(name: str, default: bool) -> bool:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def _int(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        return int(raw) if raw else default

    return EntityReviewConfig(
        enabled=_bool("ENTITY_REVIEW_ENABLED", bool(cfg.get("enabled", True))),
        llm_role=(
            os.getenv("ENTITY_REVIEW_LLM_ROLE", cfg.get("llm_role", "tags")).strip()
            or "tags"
        ),
        batch_size=_int("ENTITY_REVIEW_BATCH_SIZE", int(cfg.get("batch_size", 40))),
        apply_rule_filters=_bool(
            "ENTITY_REVIEW_RULE_FILTER",
            bool(cfg.get("apply_rule_filters", True)),
        ),
    )


def rule_clean_person_name(name: str) -> tuple[str | None, str]:
    """规则清洗人名。返回 (结果, action)；drop 时结果为 None。"""
    raw = name.strip()
    if not raw or _DROP_PERSON_RE.match(raw):
        return None, "drop"

    cleaned = _PERSON_PREFIX_RE.sub("", raw).strip()
    if not cleaned:
        return None, "drop"

    # 若仍以称谓结尾且过长，截取「末 1～3 字专名 + 称谓」
    m = _PERSON_TAIL_RE.search(cleaned)
    if m and len(cleaned) > len(m.group(1)):
        cleaned = m.group(1)

    if _DROP_PERSON_RE.match(cleaned) or len(cleaned) < 2:
        return None, "drop"

    if cleaned != raw:
        return cleaned, "rewrite"
    return cleaned, "keep"


def rule_clean_entity(item: ChunkEntityItem) -> EntityCleanOp:
    if item.entity_type == "person":
        cleaned, action = rule_clean_person_name(item.name)
        return EntityCleanOp(
            original=item.name,
            cleaned=cleaned,
            entity_type=item.entity_type,
            action=action,
            source="rule",
        )
    # place / org：暂不做激进规则，交给 LLM 或 keep
    return EntityCleanOp(
        original=item.name,
        cleaned=item.name,
        entity_type=item.entity_type,
        action="keep",
        source="rule",
    )


def _parse_llm_json(raw: str) -> dict:
    raw = (raw or "{}").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(line for line in lines if not line.strip().startswith("```"))
    return json.loads(raw)


def llm_clean_entities(
    candidates: list[tuple[str, EntityType]],
    *,
    review_cfg: EntityReviewConfig | None = None,
) -> list[EntityCleanOp]:
    """批量 LLM 清洗；candidates 为 (name, type)。"""
    review_cfg = review_cfg or resolve_entity_review_config()
    if not candidates:
        return []

    client = get_llm_client(review_cfg.llm_role)
    model = get_llm_model(review_cfg.llm_role)
    allowed = {(n, t) for n, t in candidates}
    ops: list[EntityCleanOp] = []

    for i in range(0, len(candidates), review_cfg.batch_size):
        batch = candidates[i : i + review_cfg.batch_size]
        lines = "\n".join(f"- {name}|{etype}" for name, etype in batch)
        prompt = CLEAN_PROMPT.format(lines=lines)
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
        for op in data.get("ops") or []:
            fr = str(op.get("from") or "").strip()
            et = str(op.get("type") or "person").strip()
            if et not in {"person", "place", "org"}:
                et = "person"
            if (fr, et) not in allowed:  # type: ignore[arg-type]
                # 允许 type 与候选不一致时按 from 匹配
                matched = [t for n, t in allowed if n == fr]
                if not matched:
                    continue
                et = matched[0]
            action = str(op.get("action") or "keep").strip().lower()
            to_raw = op.get("to")
            to = None if to_raw is None else str(to_raw).strip() or None
            if action == "drop":
                to = None
            elif action == "keep":
                to = fr
            elif action == "rewrite" and not to:
                action = "drop"
                to = None
            ops.append(
                EntityCleanOp(
                    original=fr,
                    cleaned=to,
                    entity_type=et,  # type: ignore[arg-type]
                    action=action if action in {"keep", "rewrite", "drop"} else "keep",
                    source="llm",
                )
            )
    return ops


def _looks_dirty(name: str, etype: EntityType) -> bool:
    if etype != "person":
        return False
    if _PERSON_PREFIX_RE.match(name):
        return True
    # 过长且含称谓，可能粘连
    if len(name) > 6 and re.search(rf"(?:{_PERSON_SUFFIX})$", name):
        return True
    return False


def review_entities(
    rows: list[ChunkEntityRow],
    review_cfg: EntityReviewConfig | None = None,
) -> EntityReviewResult:
    """对一批 chunk 实体做规则 + LLM 清洗，返回操作表。"""
    review_cfg = review_cfg or resolve_entity_review_config()
    out = EntityReviewResult(reviewed_at=datetime.now(timezone.utc).isoformat())

    # 唯一候选
    uniq: dict[tuple[str, EntityType], ChunkEntityItem] = {}
    for row in rows:
        for item in row.entities:
            uniq[(item.name, item.entity_type)] = item

    rule_ops: dict[tuple[str, EntityType], EntityCleanOp] = {}
    if review_cfg.apply_rule_filters:
        for key, item in uniq.items():
            op = rule_clean_entity(item)
            rule_ops[key] = op
            out.ops.append(op)

    # LLM：对仍脏、或规则 rewrite/drop 的人名再确认；以及未规则处理的可疑项
    llm_candidates: list[tuple[str, EntityType]] = []
    if review_cfg.enabled:
        for (name, etype), item in uniq.items():
            rule_op = rule_ops.get((name, etype))
            if rule_op and rule_op.action == "drop":
                continue  # 规则已 drop，不必再问
            # 规则已 rewrite 的也送 LLM 复核；明显干净的 keep 可跳过以省钱
            if rule_op and rule_op.action == "keep" and not _looks_dirty(name, etype):
                continue
            # 送给 LLM 的是规则清洗后的名字（若已 rewrite）
            send_name = (
                rule_op.cleaned
                if rule_op and rule_op.cleaned and rule_op.action == "rewrite"
                else name
            )
            if send_name:
                llm_candidates.append((send_name, etype))
            # 同时带上原始脏名，便于映射
            if send_name != name:
                llm_candidates.append((name, etype))

        # 去重保序
        seen: set[tuple[str, EntityType]] = set()
        deduped: list[tuple[str, EntityType]] = []
        for c in llm_candidates:
            if c not in seen:
                seen.add(c)
                deduped.append(c)

        if deduped:
            try:
                llm_ops = llm_clean_entities(deduped, review_cfg=review_cfg)
                out.ops.extend(llm_ops)
            except Exception as exc:
                print(f"  [warn] 实体 LLM 清洗失败，仅用规则: {exc}")

    return out


def apply_entity_clean_ops(
    rows: list[ChunkEntityRow],
    review: EntityReviewResult,
) -> list[ChunkEntityRow]:
    """把清洗结果应用到 chunk 行：rewrite 改名，drop 删除，同 chunk 去重。"""
    # 合并同 key 的 ops：llm 覆盖 rule
    final: dict[tuple[str, str], EntityCleanOp] = {}
    for op in review.ops:
        key = (op.original, op.entity_type)
        prev = final.get(key)
        if prev is None or op.source == "llm":
            final[key] = op

    new_rows: list[ChunkEntityRow] = []
    for row in rows:
        cleaned_items: list[ChunkEntityItem] = []
        seen: set[tuple[str, str]] = set()
        for item in row.entities:
            op = final.get((item.name, item.entity_type))
            if op is None:
                name, etype = item.name, item.entity_type
            elif op.action == "drop" or not op.cleaned:
                continue
            else:
                name, etype = op.cleaned, op.entity_type
                # 对 rewrite 后再 rule 一次，去掉残留前缀
                if etype == "person":
                    n2, act2 = rule_clean_person_name(name)
                    if act2 == "drop" or not n2:
                        continue
                    name = n2

            key = (name, etype)
            if key in seen:
                # 合并 tf
                for existing in cleaned_items:
                    if existing.name == name and existing.entity_type == etype:
                        existing.tf = max(existing.tf, item.tf)
                        break
                continue
            seen.add(key)
            cleaned_items.append(
                ChunkEntityItem(name=name, entity_type=etype, tf=item.tf)  # type: ignore[arg-type]
            )
        cleaned_items.sort(key=lambda x: (x.entity_type, -x.tf, x.name))
        new_rows.append(
            ChunkEntityRow(
                chunk_id=row.chunk_id,
                date=row.date,
                entities=cleaned_items,
                preview=row.preview,
            )
        )
    return new_rows


def summarize_review(review: EntityReviewResult) -> dict[str, Any]:
    n_drop = sum(1 for o in review.ops if o.action == "drop")
    n_rewrite = sum(1 for o in review.ops if o.action == "rewrite")
    n_keep = sum(1 for o in review.ops if o.action == "keep")
    examples = [
        f"{o.original}→{o.cleaned or '∅'}({o.action}/{o.source})"
        for o in review.ops
        if o.action in {"rewrite", "drop"}
    ][:12]
    return {
        "reviewed_at": review.reviewed_at,
        "n_ops": len(review.ops),
        "n_keep": n_keep,
        "n_rewrite": n_rewrite,
        "n_drop": n_drop,
        "examples": examples,
    }
