"""Opt-in public source reads. Never substitute contract fixtures for live facts.

The offline places package stays network-free. This adapter reads only registered
URLs and returns its existing result events. Importing it performs no I/O.
"""

import argparse
import asyncio
from datetime import date, datetime, timezone
from html.parser import HTMLParser
import json
import re
import socket
from urllib.parse import urljoin

import aiohttp

from .places import FixedClock, load_bundle
from .places.check import Checker
from .places.fixture import PageSnapshot
from .places.model import (
    MAX_EXTERNAL_READ_SECONDS, MAX_EXTRACT_CHARS, MAX_PAGES_PER_CHECK,
    Citation, NavigationRefused, SourceDisabled,
)
from .places.registry import classify_host, vet_url


# The official visit pages measured 437,639 and 132,852 bytes on 2026-10-31.
# Keep a finite transport cap while retaining the 4,000-character evidence cap.
MAX_LIVE_BODY_BYTES = 512 * 1024
_LIVE_SOURCES = frozenset({"src.nhm.visit", "src.fisher.visit"})
_REDIRECTS = {301, 302, 303, 307, 308}


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate the exact address list passed to the connector, without a second DNS lookup."""

    def __init__(self, delegate=None):
        self.delegate = delegate or aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self.delegate.resolve(host, port, family)
        if not records or any(classify_host(row["host"]) != "public" for row in records):
            raise NavigationRefused("dns_non_public")
        return records

    async def close(self):
        await self.delegate.close()


class LiveReader:
    """One total deadline for DNS, TLS, redirects and the bounded body stream."""

    async def read(self, source):
        if not source.enabled:
            raise SourceDisabled(source.disabled_reason or "disabled")
        if source.id not in _LIVE_SOURCES:
            raise SourceDisabled("live_parser_unavailable")
        if not source.allowed_urls:
            raise SourceDisabled("no_registered_url")
        resolver = PublicResolver()
        try:
            async with asyncio.timeout(MAX_EXTERNAL_READ_SECONDS):
                # No ambient proxy, cookies, credentials or conversation content.
                # The resolver's vetted addresses are the addresses used by TLS.
                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False),
                    timeout=aiohttp.ClientTimeout(total=MAX_EXTERNAL_READ_SECONDS),
                    trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                    auto_decompress=False,
                    headers={"Accept": "text/html", "Accept-Encoding": "identity",
                             "User-Agent": "Angeleno/0.1 (registered public visit pages)"},
                ) as client:
                    return await self._read(client, source)
        except TimeoutError:
            raise SourceDisabled("timeout") from None
        except aiohttp.ClientError:
            raise SourceDisabled("network_error") from None
        finally:
            await resolver.close()

    async def _read(self, client, source):
        target = source.allowed_urls[0]
        for _ in range(MAX_PAGES_PER_CHECK):
            vet_url(target, source.allowed_urls)
            async with client.get(target, allow_redirects=False) as response:
                if response.status in _REDIRECTS:
                    location = response.headers.get("Location")
                    if not location:
                        raise SourceDisabled("redirect_without_location")
                    try:
                        target = urljoin(target, location)
                        # Refuse before opening the redirected connection.
                        vet_url(target, source.allowed_urls)
                    except NavigationRefused:
                        raise
                    except ValueError:
                        # Malformed IPv6 or a nonnumeric port is source failure,
                        # not a fatal actor/voice failure. Do not echo Location.
                        raise NavigationRefused("malformed_redirect") from None
                    continue
                if response.status != 200:
                    raise SourceDisabled(f"http_{response.status}")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise SourceDisabled("encoded_body")
                if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() not in {
                    "text/html", "application/xhtml+xml",
                }:
                    raise SourceDisabled("not_html")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk[:MAX_LIVE_BODY_BYTES + 1 - len(raw)])
                    if len(raw) > MAX_LIVE_BODY_BYTES:
                        raise SourceDisabled("body_too_large")
                if not raw:
                    raise SourceDisabled("no_usable_body")
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    raise SourceDisabled("unsupported_encoding") from None
                return PageSnapshot(source.id, target, text, False, (), len(raw))
        raise SourceDisabled("redirect_limit")


class _Sections(HTMLParser):
    """Read visible heading sections; scripts, forms and social widgets never become evidence."""

    VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
    SKIP = frozenset({"script", "style", "form", "noscript", "svg", "iframe", "nav", "footer", "template"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.sections = []
        self.heading = None
        self.heading_text = []
        self.current = None
        self.parts = []
        self.modified = []
        self.ignored = 0
        self.notices = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        skip = (bool(self.stack and self.stack[-1][1]) or tag in self.SKIP
                or "data-instruction" in attrs or "hidden" in attrs
                or attrs.get("aria-hidden") == "true"
                or attrs.get("id", "").startswith("sb_instagram"))
        if tag not in self.VOID:
            if len(self.stack) >= 128:
                raise ValueError("HTML nesting limit")
            notice = (bool(self.stack and self.stack[-1][2])
                      or "bc-c-info-bar__text__inner" in attrs.get("class", "").split())
            self.stack.append((tag, skip, notice))
        if skip:
            self.ignored += 1
            return
        if tag == "meta" and attrs.get("property") == "article:modified_time":
            self.modified.append(attrs.get("content", ""))
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self.heading = tag
            self.heading_text = []
        elif tag in {"p", "div", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        skipped = bool(self.stack and self.stack[-1][1])
        # HTML void tags never change depth; malformed closures cannot unhide a script.
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if skipped:
            return
        if tag == self.heading:
            self.current = " ".join("".join(self.heading_text).split())
            self.heading = None
        if tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.stack and self.stack[-1][1]:
            return
        if self.stack and self.stack[-1][2]:
            self.notices.append(data)
        if self.heading:
            self.heading_text.append(data)
        elif self.current:
            self.parts.append(data)

    def _flush(self):
        if self.current is not None:
            lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
            self.sections.append((self.current, "\n".join(line for line in lines if line)))
        self.parts = []
        self.current = None

    def close(self):
        super().close()
        self._flush()


def _normal(text):
    return " ".join(text.replace("–", "-").replace("—", "-").replace("’", "'").split())


def live_decision(snapshot, *, source, asked, topic):
    """Conservative source-specific evidence, independent of synthetic data-claim attributes.

NHM's complete hours block has a deliberately narrow grammar. Fisher's campus
holiday dependency and conflicting FAQ remain insufficient, with the actual
hours/exception text retained. Neither unknown markup nor unknown prose becomes
a supported claim. Add new rules only from observed source evidence and tests.
"""
    parsed = _Sections()
    try:
        if snapshot.truncated or len(snapshot.text.encode("utf-8")) > MAX_LIVE_BODY_BYTES:
            raise ValueError("incomplete body")
        parsed.feed(snapshot.text)
        parsed.close()
    except ValueError:
        return {"status": "insufficient", "conclusion": None, "citations": (),
                "detail": "incomplete_or_complex_html", "incomplete": True}
    modified = None
    if len(set(parsed.modified)) == 1:
        try:
            value = datetime.fromisoformat(parsed.modified[0])
            if value.tzinfo is not None:
                modified = value.isoformat()
        except ValueError:
            pass
    heading = "Hours" if source.id == "src.nhm.visit" else "Hours and Tours"
    sections = [body for title, body in parsed.sections if title == heading]
    citations = []
    remaining = MAX_EXTRACT_CHARS
    for body in sections[:2]:
        excerpt = body[:remaining]
        remaining -= len(excerpt)
        if excerpt:
            citations.append(Citation(source.id, snapshot.url, excerpt, "page_hours"))
    citations = tuple(citations)
    result = dict(status="insufficient", conclusion=None, applicable_time=None,
                  citations=citations, page_updated_at=modified,
                  ignored_blocks=parsed.ignored, detail="unrecognized_hours")
    if topic != "hours":
        return {**result, "status": "out_of_scope", "detail": "live_question_type"}
    if asked is None:
        return {**result, "detail": "missing_applicable_date"}
    if len(sections) != 1 or not sections[0] or len(sections[0]) > MAX_EXTRACT_CHARS:
        return {**result, "detail": "missing_or_ambiguous_hours"}
    if source.id == "src.fisher.visit":
        # The page does not fully enumerate campus holidays, and its FAQ has
        # a conflicting opening time. Do not silently repair it or infer years.
        return {**result, "detail": "calendar_exceptions_unresolved"}
    text = _normal(sections[0])
    # An announcement is a separate claim, not a bag of closure keywords. Until
    # its applicability is understood, the ordinary hours cannot establish that
    # a particular visit is possible (including "We will not open tomorrow.").
    # Even traffic-only notices remain unresolved in this initial parser.
    notice = " ".join(" ".join(parsed.notices).split())
    if notice:
        notice = notice[:remaining]
        return {**result, "detail": "unresolved_notice",
                "citations": citations + (Citation(source.id, snapshot.url, notice, "notice"),)}
    pattern = (
        r"Monday-Sunday (\d{1,2}:\d{2} [AP]M)-(\d{1,2}:\d{2} [AP]M) "
        r"The Museum is closed the first Tuesday of every month except June-August\. "
        r"The Museum is also closed on Independence Day, Thanksgiving, Christmas and New Year's Day\."
    )
    match = re.fullmatch(pattern, text)
    if not match:
        return result
    try:
        start, end = [datetime.strptime(value, "%I:%M %p").time() for value in match.groups()]
    except ValueError:
        return result
    if start >= end:
        return result
    holiday = ((asked.month, asked.day) in {(1, 1), (7, 4), (12, 25)}
               or asked.month == 11 and asked.weekday() == 3 and 22 <= asked.day <= 28)
    first_tuesday = asked.weekday() == 1 and asked.day <= 7 and asked.month not in {6, 7, 8}
    hours = f"{start:%H:%M}-{end:%H:%M}"
    conclusion = (f"{asked.isoformat()}：按访问页列出的休馆规则，当天闭馆。" if holiday or first_tuesday
                  else f"{asked.isoformat()}：按访问页公布的开放规则，开放时间为 {hours}（洛杉矶时间）。")
    return {**result, "status": "supported", "conclusion": conclusion,
            "applicable_time": None if holiday or first_tuesday else hours, "detail": "published_hours_rule"}


class LiveChecker(Checker):
    """Async check with the same event/cancellation contract, never a fixture fallback."""

    def __init__(self, catalog, registry, clock, *, channel=None, reader=None):
        super().__init__(catalog, registry, None, clock, channel=channel)
        self.reader = reader or LiveReader()

    def check(self, *args, **kwargs):
        raise TypeError("LiveChecker must be awaited with check_async")

    async def check_async(self, intent):
        if self.channel and self.channel.is_cancelled(intent.request_id):
            return self._event(intent, status="cancelled", detail="cancelled")
        try:
            source = self.registry.get(intent.source_id)
        except KeyError:
            return self._event(intent, status="out_of_scope", detail="unknown_source")
        subject = self._subject(intent, source)
        if source.place_id != intent.place_id or intent.question_type not in source.question_types:
            return self._event(intent, status="out_of_scope", detail="source_scope", subject=subject)
        if intent.question_type != "hours":
            return self._event(intent, status="out_of_scope", detail="live_question_type", subject=subject)
        try:
            snapshot = await self.reader.read(source)
            fields = live_decision(snapshot, source=source, asked=intent.applicable_date,
                                   topic=intent.question_type)
        except (NavigationRefused, SourceDisabled) as exc:
            fields = {"status": "unavailable", "detail": exc.reason}
        if self.channel and self.channel.is_cancelled(intent.request_id):
            return self._event(intent, status="cancelled", detail="cancelled")
        return self._event(intent, subject=subject, **fields)

    def _checked_at(self):
        # Retrieval time is not the date asked about or a fixture clock.
        return datetime.now(timezone.utc).astimezone(self.clock.now().tzinfo).isoformat(timespec="seconds")


def live_lookup():
    """Live-only checker with an LA date anchor refreshed at each user turn."""
    from .lookup import TextLookup
    class LiveLookup(TextLookup):
        def begin_turn(self, session_id, user_turn):
            self.clock = FixedClock(datetime.now(timezone.utc))
            self.checker.clock = self.clock
            super().begin_turn(session_id, user_turn)
    bundle = load_bundle()
    service = LiveLookup(bundle, FixedClock(datetime.now(timezone.utc)))
    service.checker = LiveChecker(bundle.catalog, bundle.registry, service.clock, channel=service.channel)
    return service


async def _probe(source_id, applicable):
    service = live_lookup()
    source = service.bundle.registry.get(source_id)
    intent = service.channel.open(
        session_id=service.local_session, user_turn=1, utterance="检查登记来源开放规则",
        question="检查登记来源开放规则", place_id=source.place_id, source_id=source.id,
        question_type="hours", applicable_date=applicable,
    )
    event = await service.checker.check_async(intent)
    print(json.dumps(event.to_dict(), ensure_ascii=False, indent=2))
    return 0 if event.status == "supported" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Read a registered public page; no model call")
    parser.add_argument("--source", choices=sorted(_LIVE_SOURCES))
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args(argv)
    if not args.fetch:
        print("live source read: disabled")
        return 0
    if not args.source or not args.date:
        parser.error("--fetch requires --source and --date")
    return asyncio.run(_probe(args.source, args.date))


if __name__ == "__main__":
    raise SystemExit(main())
