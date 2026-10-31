"""Bind local card dates to the current user's words, never export them.

This is a small date provenance check, not a general intent classifier. It
cannot certify that a mentioned date is a visit date; the actor still owns
that interpretation. Missing or ambiguous provenance remains unknown.
"""
from datetime import date, timedelta
import re
from zoneinfo import ZoneInfo

from .places import resolve_when
from .places.model import LA_ZONE

_TOKEN = re.compile(r"(?<![0-9])(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|(?:[0-9]{4}年)?[0-9]{1,2}月[0-9]{1,2}[日号]|(?:下(?:个)?|这)?(?:周|星期|礼拜)[一二三四五六天日]|今天|明天|昨天)(?![0-9])")
_CHINESE = re.compile(r"(?:(?P<year>[0-9]{4})年)?(?P<month>[0-9]{1,2})月(?P<day>[0-9]{1,2})[日号]")
# Conservative display guards, not a semantic parser. A clear negative or
# unsettled date leaves the single-day card blank. Ranges may abbreviate their
# second endpoint, so token counting alone is insufficient.
_UNCONFIRMED = re.compile(r"不是|不去|不在|不要|别去|不打算|没定|未定|待定|没确定|未确定|不确定|再定")
_RANGE_TAIL = re.compile(r"^(?:到|至|[-—–~～]|或(?:者)?|还是|、|和)(?:[0-9]|下|这|周|星期|礼拜)")


def _resolve(text, now):
    chinese = _CHINESE.fullmatch(text)
    if chinese:
        return date(int(chinese['year'] or now.astimezone(ZoneInfo(LA_ZONE)).year),
                    int(chinese['month']), int(chinese['day']))
    result = resolve_when(text, now=now)
    return None if result.needs_clarification else result.date


def bind_local_date(when, utterance, *, now, previous_date=None, revising=False):
    if when is None:
        return None, 'not_requested'
    try:
        requested = _resolve(when, now)
        if requested is None:
            return None, 'unparsed'
        text = ''.join(utterance.split())
        # A narrow correction inherits only a previously bound query's day,
        # never a date from the persona, arbitrary history, or a cancelled line.
        if (revising and previous_date is not None and
                re.fullmatch(r'(?:我是说|改成|改到)下周(?:[，,]?不是这周)?[。.!！?？]?', text)):
            shifted = previous_date + timedelta(days=7)
            if requested == shifted:
                return shifted, 'current_user_week_correction'
        matches = list(_TOKEN.finditer(text))
        if _UNCONFIRMED.search(text) or any(_RANGE_TAIL.match(text[m.end():]) for m in matches):
            return None, 'unbound_or_ambiguous'
        mentioned = {_resolve(match.group(), now) for match in matches}
        mentioned.discard(None)
        if mentioned == {requested}:
            return requested, 'current_user'
    except (ValueError, TypeError):
        return None, 'unparsed'
    return None, 'unbound_or_ambiguous'
