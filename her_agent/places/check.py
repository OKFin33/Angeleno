"""Current-check contract over frozen fixtures and a fixed clock.

The checker does not open sockets, does not call a model, and does not write
the registry. Cancellation is best-effort: a request that is already cancelled
is not read, and a result that appears anyway is still unusable by the channel.
"""

from .extract import decide, extract_html
from .model import (
    CHANNEL, LA_ZONE, MAX_BODY_BYTES, MAX_EXTERNAL_READ_SECONDS, NavigationRefused,
    PlacesError, ResultEvent, SourceDisabled,
)


_CACHEABLE = frozenset({"supported", "insufficient", "conflict"})


def external_read_exhausted(elapsed_seconds):
    """True when a live read has used the frozen external-read budget."""
    if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, (int, float)):
        raise PlacesError("elapsed seconds required")
    if elapsed_seconds < 0:
        raise PlacesError("elapsed seconds required")
    return elapsed_seconds >= MAX_EXTERNAL_READ_SECONDS


class Checker:
    def __init__(self, catalog, registry, library, clock, channel=None):
        from .clock import FixedClock
        if not isinstance(clock, FixedClock):
            raise TypeError("places checks require a FixedClock")
        self.catalog = catalog
        self.registry = registry
        self.library = library
        self.clock = clock
        self.channel = channel
        self._cache = {}

    def check(self, intent, *, recheck=False, max_body_bytes=MAX_BODY_BYTES):
        if self.channel is not None and self.channel.is_cancelled(intent.request_id):
            return self._event(intent, status="cancelled", detail="cancelled")
        if not intent.source_id:
            return self._event(intent, status="out_of_scope", detail="no_source")
        source = self.registry.get(intent.source_id)
        if intent.place_id is not None and source.place_id != intent.place_id:
            return self._event(intent, status="out_of_scope", detail="place_source_mismatch")
        subject = self._subject(intent, source)
        if not source.enabled:
            return self._event(intent, status="unavailable", detail=source.disabled_reason, subject=subject)
        if intent.question_type not in source.question_types:
            return self._event(intent, status="out_of_scope", detail="question_type", subject=subject)
        key = (
            intent.session_id, intent.place_id, intent.source_id, intent.question_type,
            None if intent.applicable_date is None else intent.applicable_date.isoformat(),
        )
        use_cache = max_body_bytes == MAX_BODY_BYTES
        if use_cache and not recheck and key in self._cache:
            stored = dict(self._cache[key])
            stored["detail"] = "reused"
            return self._event(intent, subject=subject, **stored)
        try:
            snapshot = self.library.read(source, max_body_bytes=max_body_bytes)
        except SourceDisabled as exc:
            return self._event(intent, status="unavailable", detail=exc.reason, subject=subject)
        except NavigationRefused as exc:
            return self._event(intent, status="unavailable", detail=exc.reason, subject=subject)
        extracted, incomplete = extract_html(snapshot.text, truncated=snapshot.truncated)
        decision = decide(
            extracted, incomplete, topic=intent.question_type, asked=intent.applicable_date,
            source_id=source.id, url=snapshot.url,
        )
        fields = {
            "status": decision.status,
            "conclusion": decision.conclusion,
            "applicable_time": decision.applicable_time,
            "checked_at": self._checked_at(),
            "page_updated_at": extracted.page_updated_at,
            "citations": tuple(decision.citations),
            "discarded": decision.discarded,
            "conflicts": decision.conflicts,
            "refused_navigations": snapshot.refused_navigations,
            "ignored_blocks": extracted.ignored_blocks,
            "incomplete": decision.incomplete,
            "detail": decision.detail or extracted.detail,
        }
        if use_cache and decision.status in _CACHEABLE and not decision.incomplete:
            self._cache[key] = fields
        return self._event(intent, subject=subject, **fields)

    def _checked_at(self):
        return self.clock.now(LA_ZONE).isoformat(timespec="seconds")

    def _subject(self, intent, source):
        entry = self.catalog.by_id.get(source.place_id)
        name = entry.name if entry is not None else source.institution
        return f"{name} · {intent.question_type}"

    def _event(self, intent, *, subject=None, **fields):
        status = fields.pop("status")
        values = {
            "conclusion": None,
            "applicable_time": None,
            "checked_at": None,
            "page_updated_at": None,
            "citations": (),
            "discarded": (),
            "conflicts": (),
            "refused_navigations": (),
            "ignored_blocks": 0,
            "incomplete": False,
            "detail": None,
        }
        values.update(fields)
        if status == "cancelled":
            values["conclusion"] = None
            values["checked_at"] = None
        elif values["checked_at"] is None:
            values["checked_at"] = self._checked_at()
        return ResultEvent(
            channel=CHANNEL,
            session_id=intent.session_id,
            user_turn=intent.user_turn,
            request_id=intent.request_id,
            version=intent.version,
            lineage_id=intent.lineage_id,
            status=status,
            subject=subject if subject is not None else intent.question,
            applicable_date=None if intent.applicable_date is None else intent.applicable_date.isoformat(),
            **values,
        )
