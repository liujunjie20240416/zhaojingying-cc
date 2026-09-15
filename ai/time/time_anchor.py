"""Relative-date staleness guard for semantic facts.

Facts written before this guard existed can contain frozen relative dates
("用户本周过生日", "大白鹅当天去做核酸") with no absolute anchor.  Served
verbatim, they read as if they were current, so retrieval should penalise or
annotate them instead of presenting a stale "本周/当天" as fresh.

The write-side prompts (import chunk analysis and online reflection) now
require anchoring relative dates to absolute ones; this module is the
read-side safety net for legacy facts and for misses that slip through.
"""

import re

# Explicit multi-character relative terms, each anchored to the moment the
# fact was written.  Single-character fragments are excluded on purpose:
# a bare "周" would match "周末" and a bare "天" would match "全天".
_RELATIVE_TERMS = [
    "本周", "这周", "上周", "下周", "这礼拜", "上礼拜", "下礼拜",
    "今天", "昨天", "明天", "前天", "后天", "当天", "当晚", "当日",
    "今年", "去年", "明年", "前年",
    "这个月", "下个月", "上个月", "本月",
    "最近", "这几天", "近几天", "前阵子", "前些天", "这段时间", "这一阵",
]

# A fact carrying any of these is considered anchored: a year ("2024年考研"),
# a full date ("2024-02-01", "2024年2月1日") or a month-day ("2月3号").
_ABSOLUTE_DATE = re.compile(
    r"(?:19|20)\d{2}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?"
    r"|(?:19|20)\d{2}年"
    r"|\d{1,2}月\d{1,2}[日号]"
)

STALE_ANNOTATION = "（时间不确定，可能已过期）"


def find_unanchored_relative_time(fact: str) -> str | None:
    """Return the first unanchored relative-time term in ``fact``.

    A fact carrying an absolute date is considered anchored; relative words
    next to it only qualify that date and are safe.  Returns None for empty
    or anchored text.
    """
    if not fact:
        return None
    if _ABSOLUTE_DATE.search(fact):
        return None
    for term in _RELATIVE_TERMS:
        if term in fact:
            return term
    return None


def annotate_relative_time_fact(fact: str) -> str:
    """Append a staleness note when a fact's only time info is relative."""
    if not fact or find_unanchored_relative_time(fact) is None:
        return fact
    if STALE_ANNOTATION in fact:
        return fact
    return f"{fact}{STALE_ANNOTATION}"
