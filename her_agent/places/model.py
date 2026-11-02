"""Frozen values for the offline place contract.

Speech stays out of this module. A result event is a source-status record, not
a line for playback.
"""

from dataclasses import dataclass
from datetime import date


LA_ZONE = "America/Los_Angeles"
CHANNEL = "source_status"
PARSER_ID = "places.extract.v1"
STATUSES = frozenset({
    "evidence",
    "supported",
    "insufficient",
    "conflict",
    "unavailable",
    "out_of_scope",
    "cancelled",
})

MAX_QUESTION_CHARS = 500
MAX_BODY_BYTES = 65_536
MAX_EXTRACT_CHARS = 4_000
MAX_PAGES_PER_CHECK = 2
MAX_EXTERNAL_READ_SECONDS = 10


class PlacesError(ValueError):
    """A contract input cannot be used."""


class NavigationRefused(PlacesError):
    """A URL, redirect, or embedded navigation is not readable."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class SourceDisabled(PlacesError):
    """A registered source is present and explicitly not readable."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Citation:
    source_id: str
    url: str
    excerpt: str
    claim_kind: str

    def to_dict(self):
        return {
            "source_id": self.source_id,
            "url": self.url,
            "excerpt": self.excerpt,
            "claim_kind": self.claim_kind,
        }


@dataclass(frozen=True)
class QueryIntent:
    """One version of a check. `utterance` is the original user wording."""

    session_id: str
    user_turn: int
    request_id: str
    version: int
    lineage_id: str
    utterance: str
    question: str
    place_id: str | None
    source_id: str | None
    question_type: str
    applicable_date: date | None
    stated_zone: str | None
    supersedes: str | None


@dataclass(frozen=True)
class ResultEvent:
    """Independent source-status event. It has no speech payload."""

    channel: str
    session_id: str
    user_turn: int
    request_id: str
    version: int
    lineage_id: str
    status: str
    subject: str
    conclusion: str | None
    applicable_date: str | None
    applicable_time: str | None
    checked_at: str | None
    page_updated_at: str | None
    citations: tuple
    discarded: tuple
    conflicts: tuple
    refused_navigations: tuple
    ignored_blocks: int
    incomplete: bool
    detail: str | None

    def speech_text(self):
        return None

    def to_dict(self):
        body = {
            "channel": self.channel,
            "session_id": self.session_id,
            "user_turn": self.user_turn,
            "request_id": self.request_id,
            "version": self.version,
            "lineage_id": self.lineage_id,
            "status": self.status,
            "subject": self.subject,
            "conclusion": self.conclusion,
            "applicable_date": self.applicable_date,
            "applicable_time": self.applicable_time,
            "checked_at": self.checked_at,
            "page_updated_at": self.page_updated_at,
            "citations": [item.to_dict() for item in self.citations],
            "discarded": list(self.discarded),
            "conflicts": list(self.conflicts),
            "refused_navigations": list(self.refused_navigations),
            "ignored_blocks": self.ignored_blocks,
            "incomplete": self.incomplete,
            "detail": self.detail,
        }
        if "tts" in body or "speech" in body:
            raise PlacesError("result events cannot carry speech")
        return body
