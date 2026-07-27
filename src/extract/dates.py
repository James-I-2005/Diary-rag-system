"""日期解析与正文按日期标题切分。

尽量覆盖常见变体：
- 分隔符：- / . _ 空白 全角点 间隔号 等
- 排列：YMD（优先）/ DMY / MDY（年在末且可消歧）
- 中文：2026年7月3日 / 2026年7月3号
- 紧凑：20260703
- Markdown：# / ## 前缀、【】[] 包裹
- 标题行：日期后可跟 : ： - — _ | 与标题
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path


@dataclass
class DatedSegment:
    date: str
    content: str


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 通用分隔（半角/全角）
_SEP = r"[\s.\-/_‧·．、]"


def is_valid_date(date_str: str) -> bool:
    s = (date_str or "").strip()
    if not _DATE_RE.match(s):
        return False
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        _date(y, m, d)
    except ValueError:
        return False
    return True


def _normalize_ymd(y: int | str, m: int | str, d: int | str) -> str | None:
    try:
        out = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except (TypeError, ValueError):
        return None
    return out if is_valid_date(out) else None


def _resolve_mdy_or_dmy(a: int, b: int, y: int) -> str | None:
    """年在末尾时消歧：a/b 为月日或日月。"""
    # a 不可能是月 → 日/月/年
    if a > 12 and 1 <= b <= 12:
        return _normalize_ymd(y, b, a)
    # b 不可能是月 → 月/日/年
    if b > 12 and 1 <= a <= 12:
        return _normalize_ymd(y, a, b)
    # 皆可：中文语境优先 日/月/年（3.7.2026 = 7月3日）
    if 1 <= a <= 31 and 1 <= b <= 12:
        dmy = _normalize_ymd(y, b, a)
        if dmy:
            return dmy
    if 1 <= a <= 12 and 1 <= b <= 31:
        return _normalize_ymd(y, a, b)
    return None


# ---------------------------------------------------------------------------
# 从任意短文本中抽取第一个完整日期（路径 / 行首共用）
# ---------------------------------------------------------------------------

# (name, pattern, handler)
_FLEX_PATTERNS: list[tuple[str, re.Pattern[str], object]] = []


def _build_flex_patterns() -> list[tuple[str, re.Pattern[str], object]]:
    items: list[tuple[str, re.Pattern[str], object]] = []

    def add(name: str, pat: str, handler):
        items.append((name, re.compile(pat), handler))

    # 中文 YMD：2026年7月3日 / 2026 年 07 月 03 号
    add(
        "cn_ymd",
        rf"(?<!\d)(?P<y>\d{{4}})\s*年\s*(?P<m>\d{{1,2}})\s*月\s*(?P<d>\d{{1,2}})\s*[日号]?",
        lambda m: _normalize_ymd(m.group("y"), m.group("m"), m.group("d")),
    )
    # 紧凑 YYYYMMDD
    add(
        "compact",
        r"(?<!\d)(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})(?!\d)",
        lambda m: _normalize_ymd(m.group("y"), m.group("m"), m.group("d")),
    )
    # YMD 带分隔：2026-07-03 / 2026.7.3 / 2026 / 7 / 3
    add(
        "ymd_sep",
        rf"(?<!\d)(?P<y>\d{{4}}){_SEP}+(?P<m>\d{{1,2}}){_SEP}+(?P<d>\d{{1,2}})(?!\d)",
        lambda m: _normalize_ymd(m.group("y"), m.group("m"), m.group("d")),
    )
    # 年在末：3.7.2026 / 07-03-2026 / 7/3/2026
    add(
        "xxy_sep",
        rf"(?<!\d)(?P<a>\d{{1,2}}){_SEP}+(?P<b>\d{{1,2}}){_SEP}+(?P<y>\d{{4}})(?!\d)",
        lambda m: _resolve_mdy_or_dmy(int(m.group("a")), int(m.group("b")), int(m.group("y"))),
    )
    return items


_FLEX_PATTERNS = _build_flex_patterns()

# 仅月日（需 fallback_year）
_MD_CN = re.compile(
    r"(?<!\d)(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*[日号]?"
)
_MD_SEP = re.compile(
    rf"(?<!\d)(?P<m>\d{{1,2}}){_SEP}+(?P<d>\d{{1,2}})(?!\d)"
)


def parse_flexible_date(
    text: str,
    *,
    fallback_year: int | None = None,
) -> str | None:
    """从文本中取第一个可解析的完整日期。"""
    if not (text or "").strip():
        return None
    s = text.strip()
    for _name, pat, handler in _FLEX_PATTERNS:
        m = pat.search(s)
        if not m:
            continue
        date = handler(m)
        if date:
            return date
    if fallback_year is not None:
        m = _MD_CN.search(s)
        if m:
            date = _normalize_ymd(fallback_year, m.group("m"), m.group("d"))
            if date:
                return date
        m = _MD_SEP.search(s)
        if m:
            # 避免把 2~3 这类误伤：~ 不在 _SEP 里
            date = _normalize_ymd(fallback_year, m.group("m"), m.group("d"))
            if date:
                return date
    return None


# ---------------------------------------------------------------------------
# 正文：按「日期标题行」切分
# ---------------------------------------------------------------------------

# 行首可有 markdown / 包裹；日期后可接标题
_LINE_PREFIX = r"^\s*(?:#{1,6}\s*|【\s*|\[\s*)?"
_LINE_WRAP_END = r"(?:\s*[】\]])?"
# 日期后允许的标题分隔
_LINE_TITLE_TAIL = r"(?:\s*[:：\-—_|／/].*)?\s*$"


def _match_heading_date_on_line(line: str) -> tuple[str, int] | None:
    """
    若整行是日期标题（可带 #/【】与后缀标题），返回 (date, match_end_in_line)。
    """
    raw = line.rstrip("\n")
    if not raw.strip():
        return None

    # 先剥前缀再匹配日期本体，再检查后缀
    m_prefix = re.match(_LINE_PREFIX, raw)
    start = m_prefix.end() if m_prefix else 0
    rest = raw[start:]

    for _name, pat, handler in _FLEX_PATTERNS:
        m = pat.match(rest)
        if not m:
            continue
        date = handler(m)
        if not date:
            continue
        after = rest[m.end() :]
        # 允许：行结束 / 空白结束 / 包裹符 / 标题分隔
        if re.match(rf"{_LINE_WRAP_END}{_LINE_TITLE_TAIL}", after):
            # match 覆盖到原 line 的终点用于切分：日期 token 结束处
            content_start_in_line = start + m.end()
            # 吃掉包裹与紧随空白，标题本身留在本段或下一逻辑——标题跟在日期同行时归入本段正文
            # 切分时 content 从「日期 token 结束」开始，含同行标题，这是期望行为
            return date, content_start_in_line
    return None


def _collect_content_date_markers(
    text: str,
    date_pattern: str,
) -> list[tuple[int, int, str]]:
    """收集正文日期标题：(match_start, content_start, date)。"""
    markers: list[tuple[int, int, str]] = []
    seen_starts: set[int] = set()

    def _add(start: int, content_start: int, date: str | None) -> None:
        if not date or not is_valid_date(date):
            return
        if start in seen_starts:
            return
        seen_starts.add(start)
        markers.append((start, content_start, date))

    # 1) 配置 diary.date_pattern
    try:
        cfg_pat = re.compile(date_pattern, re.MULTILINE)
    except re.error:
        cfg_pat = None
    if cfg_pat is not None:
        for m in cfg_pat.finditer(text):
            date = m.group(1) if m.lastindex and m.lastindex >= 1 else None
            if date and not is_valid_date(date) and m.lastindex and m.lastindex >= 3:
                date = _normalize_ymd(m.group(1), m.group(2), m.group(3))
            if not date or not is_valid_date(date):
                # 整段匹配再试柔性解析
                date = parse_flexible_date(m.group(0))
            _add(m.start(), m.end(), date)

    # 2) 逐行柔性标题
    offset = 0
    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        hit = _match_heading_date_on_line(line_body)
        if hit:
            date, content_off = hit
            _add(offset, offset + content_off, date)
        offset += len(line)

    markers.sort(key=lambda x: x[0])
    return markers


def split_text_by_date_pattern(
    text: str,
    date_pattern: str,
) -> list[DatedSegment]:
    """按正文日期标题切分；支持多种日期写法。无匹配则返回空列表。"""
    markers = _collect_content_date_markers(text, date_pattern)
    if not markers:
        return []

    segments: list[DatedSegment] = []
    for i, (_m_start, content_start, date_str) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(text)
        content = text[content_start:end].strip()
        # 去掉行首残留的包裹符
        content = re.sub(r"^[】\]]\s*", "", content).strip()
        if content:
            segments.append(DatedSegment(date=date_str, content=content))
    return segments


def read_and_split(filepath: str | Path, date_pattern: str) -> list[DatedSegment]:
    text = Path(filepath).read_text(encoding="utf-8")
    return split_text_by_date_pattern(text, date_pattern)


# ---------------------------------------------------------------------------
# 路径 / 文件名
# ---------------------------------------------------------------------------

_YEAR_ONLY = re.compile(r"^\d{4}$")
_MONTH_ONLY = re.compile(r"^(0?[1-9]|1[0-2])$")
_DAY_ONLY = re.compile(r"^(0?[1-9]|[12]\d|3[01])$")
_YM_FOLDER = re.compile(rf"^(\d{{4}})(?:{_SEP})?(\d{{1,2}})$")


def parse_date_from_rel_path(
    rel_path: str,
    *,
    fallback_year: int | None = None,
) -> str | None:
    """从相对路径解析日记日期（文件名优先，再目录组合）。"""
    path = (rel_path or "").replace("\\", "/").strip("/")
    if not path:
        return None
    parts = path.split("/")
    filename = parts[-1]
    stem = Path(filename).stem

    hit = parse_flexible_date(stem, fallback_year=fallback_year)
    if hit:
        return hit

    for part in reversed(parts):
        name = Path(part).stem if part == filename else part
        hit = parse_flexible_date(name, fallback_year=fallback_year)
        if hit:
            return hit

    if len(parts) >= 2:
        day_part = stem
        parent = parts[-2]
        if len(parts) >= 3 and _YEAR_ONLY.match(parts[-3]) and _MONTH_ONLY.match(parts[-2]):
            if _DAY_ONLY.match(day_part):
                date = _normalize_ymd(parts[-3], parts[-2], day_part)
                if date:
                    return date
        ym = _YM_FOLDER.match(parent)
        if ym and _DAY_ONLY.match(day_part):
            date = _normalize_ymd(ym.group(1), ym.group(2), day_part)
            if date:
                return date
        if _YEAR_ONLY.match(parent):
            hit = parse_flexible_date(day_part, fallback_year=int(parent))
            if hit:
                return hit

    return None


def entry_id(date: str, path: str, index: int) -> str:
    """Manifest entry id：date__path_slug__index。"""
    slug = path.replace("\\", "/").replace("/", "_").replace(" ", "_")
    return f"{date}__{slug}__{index}"
