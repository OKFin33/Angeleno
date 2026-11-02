"""Read frozen page fixtures. This module never opens a socket."""

from dataclasses import dataclass
import hashlib
from pathlib import Path

import re

from .model import MAX_BODY_BYTES, MAX_PAGES_PER_CHECK, NavigationRefused, PlacesError, SourceDisabled
from .registry import vet_url


@dataclass(frozen=True)
class SnapshotProvenance:
    """An observation made by trusted adapter code, never parsed page JSON."""
    kind: str
    content_sha256: str

    def __post_init__(self):
        if (not isinstance(self.kind, str) or self.kind not in {"synthetic_fixture", "live_http_page"}
                or not isinstance(self.content_sha256, str) or not re.fullmatch("[0-9a-f]{64}", self.content_sha256)):
            raise ValueError("Explicit adapter observation and body digest required")


@dataclass(frozen=True)
class PageSnapshot:
    source_id: str
    url: str
    text: str
    truncated: bool
    refused_navigations: tuple
    bytes_read: int
    provenance: SnapshotProvenance | None = None


class FixtureLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.opened = []
        self.reads = 0

    def read(self, source, *, hops=(), max_body_bytes=MAX_BODY_BYTES):
        if not source.enabled:
            raise SourceDisabled(source.disabled_reason or "disabled")
        chain = tuple(hops) if hops else tuple(source.redirect_hops)
        targets = chain or source.allowed_urls
        if len(targets) > MAX_PAGES_PER_CHECK:
            raise PlacesError("a check can read at most two pages")
        for target in targets:
            vet_url(target, source.allowed_urls)
        path = self.root / "fixtures" / source.fixture
        self.opened.append(path)
        self.reads += 1
        raw = path.read_bytes()
        truncated = len(raw) > max_body_bytes
        if truncated:
            raw = raw[:max_body_bytes]
            while raw and raw[-1] & 0xC0 == 0x80:
                raw = raw[:-1]
        text = raw.decode("utf-8")
        refused = []
        for target in _refresh_targets(text):
            try:
                vet_url(target, source.allowed_urls)
            except NavigationRefused:
                refused.append(target)
        return PageSnapshot(
            source_id=source.id,
            url=source.allowed_urls[0],
            text=text,
            truncated=truncated,
            refused_navigations=tuple(refused),
            bytes_read=len(raw),
            provenance=SnapshotProvenance("synthetic_fixture", hashlib.sha256(raw).hexdigest()),
        )


def _refresh_targets(text):
    """Collect meta refresh targets without fetching them."""
    found = []
    for match in re.finditer(r"(?is)<meta\b[^>]*>", text):
        tag = match.group(0)
        if not re.search(r"(?is)\bhttp-equiv\s*=\s*['\"]?\s*refresh\b", tag):
            continue
        content = re.search(r"(?is)\bcontent\s*=\s*(['\"])(.*?)\1", tag)
        if not content:
            continue
        pieces = content.group(2).split(";", 1)
        if len(pieces) == 2 and "=" in pieces[1]:
            target = pieces[1].split("=", 1)[1].strip()
            if target:
                found.append(target)
    return found
