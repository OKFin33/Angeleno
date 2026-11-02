"""Los Angeles civil dates from an injected clock.

Weekday phrases are fixed here so tests do not depend on the wall clock:
bare weekday means the next one including today; 这周 means the Monday-Sunday
week that contains today; 下周 means seven days after that. A trailing
上午／下午／中午／晚上／白天 is kept as an uninterpreted hint.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from .model import LA_ZONE, PlacesError


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NEXT = re.compile(r"^下(?:个)?(?:星期|礼拜|周)([一二三四五六天日])$")
_THIS = re.compile(r"^这(?:个)?(?:星期|礼拜|周)([一二三四五六天日])$")
_BARE = re.compile(r"^(?:星期|礼拜|周)([一二三四五六天日])$")
_SUFFIXES = ("上午", "下午", "中午", "晚上", "白天")
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "天": 6, "日": 6}


@dataclass(frozen=True)
class WhenResolution:
    date: date | None
    needs_clarification: bool
    zone: str
    stated_zone: str | None
    time_hint: str | None
    reason: str | None


class FixedClock:
    """Aware instant chosen by the caller. This type never reads the wall clock."""

    def __init__(self, instant):
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise PlacesError("FixedClock requires an aware datetime")
        self._instant = instant

    def now(self, zone=LA_ZONE):
        return self._instant.astimezone(ZoneInfo(zone))

    def advance(self, *, seconds):
        self._instant = self._instant + timedelta(seconds=seconds)


def resolve_when(expr, *, now, zone=LA_ZONE, stated_zone=None):
    """Resolve one date expression. Unparsed or zone-split relatives stay unresolved."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PlacesError("resolve_when requires an aware datetime")
    if not isinstance(expr, str) or not expr.strip():
        raise PlacesError("date expression required")
    text, time_hint = _split_hint("".join(expr.split()))
    local_now = now.astimezone(ZoneInfo(zone))
    stated_now = now.astimezone(ZoneInfo(stated_zone)) if stated_zone else None
    absolute = _absolute(text)
    relative = absolute is None and (_relative_day(text) is not None or _weekday(text, local_now.date()) is not None)
    if relative and stated_now is not None and stated_now.date() != local_now.date():
        return WhenResolution(None, True, zone, stated_zone, time_hint, "zone_changes_date")
    if absolute is not None:
        return WhenResolution(absolute, False, zone, stated_zone, time_hint, None)
    if text == "今天":
        return _ok(local_now.date(), zone, stated_zone, time_hint)
    if text == "明天":
        return _ok(local_now.date() + timedelta(days=1), zone, stated_zone, time_hint)
    if text == "昨天":
        return _ok(local_now.date() - timedelta(days=1), zone, stated_zone, time_hint)
    weekday = _weekday(text, local_now.date())
    if weekday is not None:
        return _ok(weekday, zone, stated_zone, time_hint)
    return WhenResolution(None, True, zone, stated_zone, time_hint, "unparsed")


def _ok(value, zone, stated_zone, time_hint):
    return WhenResolution(value, False, zone, stated_zone, time_hint, None)


def _split_hint(text):
    for suffix in _SUFFIXES:
        if text.endswith(suffix) and text != suffix:
            return text[: -len(suffix)], suffix
    return text, None


def _absolute(text):
    if not _ISO.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise PlacesError("invalid calendar date") from None


def _relative_day(text):
    if text in {"今天", "明天", "昨天"}:
        return text
    return None


def _weekday(text, anchor):
    kind = None
    token = None
    for pattern, name in ((_NEXT, "next"), (_THIS, "this"), (_BARE, "bare")):
        match = pattern.fullmatch(text)
        if match:
            kind = name
            token = match.group(1)
            break
    if kind is None:
        return None
    monday = anchor - timedelta(days=anchor.weekday())
    this = monday + timedelta(days=_WEEKDAY[token])
    if kind == "this":
        return this
    if kind == "next":
        return this + timedelta(days=7)
    if this >= anchor:
        return this
    return this + timedelta(days=7)
