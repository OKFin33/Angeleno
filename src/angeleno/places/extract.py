"""Claim extractor for frozen pages.

Free prose, scripts, forms, and instruction blocks are data. They do not
become a supported conclusion and they do not change the registry. Equal-rank
claims that disagree stay a conflict; this extractor does not paraphrase them
or expand weekday ranges.
"""

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

from .model import MAX_EXTRACT_CHARS, Citation


_KINDS = frozenset({"standing", "exception", "undated", "social"})


@dataclass(frozen=True)
class Claim:
    kind: str
    topic: str
    date: date | None
    time: str | None
    posted: date | None
    text: str


@dataclass(frozen=True)
class PageExtract:
    claims: tuple
    page_updated_at: str | None
    ignored_blocks: int
    detail: str | None


@dataclass(frozen=True)
class Decision:
    status: str
    conclusion: str | None
    applicable_time: str | None
    citations: tuple
    discarded: tuple
    conflicts: tuple
    incomplete: bool
    detail: str | None


def extract_html(text, *, truncated=False):
    parser = _Claims()
    parser.feed(text)
    parser.close()
    updated, detail = _updated(parser.page_updated)
    claims = []
    incomplete = truncated
    for claim in parser.claims:
        if len(claim.text) > MAX_EXTRACT_CHARS:
            incomplete = True
            claim = Claim(claim.kind, claim.topic, claim.date, claim.time, claim.posted,
                          claim.text[:MAX_EXTRACT_CHARS])
        claims.append(claim)
    if incomplete and detail is None:
        detail = "truncated"
    return PageExtract(tuple(claims), updated, parser.ignored_blocks, detail), incomplete


def decide(extract, incomplete, *, topic, asked, source_id, url):
    """Pick a claim by rank. Dated exceptions beat standing; social and undated do not override."""
    relevant = [claim for claim in extract.claims if claim.topic == topic]
    exceptions = [claim for claim in relevant if claim.kind == "exception" and claim.date == asked]
    standing = [claim for claim in relevant if claim.kind == "standing"]
    undated = [claim for claim in relevant if claim.kind == "undated"]
    social = [claim for claim in relevant if claim.kind == "social"]
    source_citation = ( _cite(source_id, url, "", "source"), )
    if incomplete:
        retained = tuple(
            _cite(source_id, url, claim.text, claim.kind)
            for claim in relevant if claim.kind == "exception"
        )
        return Decision(
            "insufficient", None, None, source_citation + retained,
            tuple(claim.text for claim in standing + undated + social),
            (), True, "truncated",
        )
    if topic == "hours" and asked is None:
        return Decision("insufficient", None, None, source_citation, (), (), False, "missing_applicable_date")
    if exceptions:
        return _from_rank(exceptions, standing + undated + social, source_id, url, asked_wins=True)
    if not standing and not undated:
        return Decision("insufficient", None, None, source_citation, tuple(claim.text for claim in social),
                        (), False, "no_claim")
    if standing:
        return _from_rank(standing, undated + social, source_id, url, asked_wins=False)
    texts = {claim.text for claim in undated}
    discarded = tuple(claim.text for claim in social + undated)
    if len(texts) == 1:
        return Decision("insufficient", None, None, source_citation, discarded, (), False, "undated_only")
    return Decision("conflict", None, None, source_citation, tuple(claim.text for claim in social),
                    tuple(claim.text for claim in undated), False, "undated_conflict")


def _from_rank(winners, losers, source_id, url, *, asked_wins):
    del asked_wins
    unique = []
    for claim in winners:
        if claim.text not in unique:
            unique.append(claim.text)
    discarded = tuple(claim.text for claim in losers if claim.text not in unique)
    if len(unique) == 1:
        claim = next(item for item in winners if item.text == unique[0])
        return Decision(
            "supported", claim.text, claim.time,
            (_cite(source_id, url, claim.text, claim.kind),),
            discarded, (), False, None,
        )
    return Decision(
        "conflict", None, None,
        tuple(_cite(source_id, url, text, winners[0].kind) for text in unique),
        discarded, tuple(unique), False, "equal_rank",
    )


def _cite(source_id, url, excerpt, kind):
    return Citation(source_id, url, excerpt, kind)


def _updated(values):
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return None, None
    if any(value != cleaned[0] for value in cleaned):
        return None, "page_updated_conflict"
    token = cleaned[0]
    try:
        date.fromisoformat(token[:10])
    except ValueError:
        return None, "page_updated_unreadable"
    return token, None


class _Claims(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.claims = []
        self.page_updated = []
        self.ignored_blocks = 0
        self._skip = None
        self._depth = 0
        self._capture = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        attr = {}
        for key, value in attrs:
            if key in attr:
                continue
            attr[key.lower()] = value or ""
        if self._skip is not None:
            self._depth += 1
            return
        if tag in {"script", "style", "form", "noscript"} or "data-instruction" in attr:
            self._skip = tag
            self._depth = 1
            self.ignored_blocks += 1
            return
        if tag == "meta":
            if attr.get("name", "").lower() == "page-updated":
                self.page_updated.append(attr.get("content", ""))
            return
        if tag == "article" and "data-claim" in attr:
            self._capture = attr
            self._buf = []

    def handle_endtag(self, tag):
        if self._skip is not None:
            self._depth -= 1
            if self._depth <= 0:
                self._skip = None
                self._depth = 0
            return
        if tag == "article" and self._capture is not None:
            self._finish()

    def handle_data(self, data):
        if self._skip is None and self._capture is not None:
            self._buf.append(data)

    def _finish(self):
        attr = self._capture
        self._capture = None
        kind = attr.get("data-claim", "")
        topic = attr.get("data-topic", "")
        text = " ".join("".join(self._buf).split())
        if kind not in _KINDS or not topic or not text:
            return
        try:
            claim_date = date.fromisoformat(attr["data-date"]) if attr.get("data-date") else None
            posted = date.fromisoformat(attr["data-posted"]) if attr.get("data-posted") else None
        except ValueError:
            return
        if kind == "exception" and claim_date is None:
            return
        moment = attr.get("data-time") or None
        if moment is not None and len(moment) > 32:
            return
        self.claims.append(Claim(kind, topic, claim_date, moment, posted, text))
