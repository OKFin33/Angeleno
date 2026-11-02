"""Registered sources and the URL gate.

Only an allowed public http(s) URL may be read. Literal private, loopback,
link-local, non-global, and local names are refused before any file is opened.
Hostnames are not resolved, so a public name that would point at a private
address is outside this contract.
"""

from dataclasses import dataclass
from datetime import datetime
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .model import MAX_PAGES_PER_CHECK, PARSER_ID, NavigationRefused, PlacesError


_REASONS = frozenset({"timeout", "http_403", "no_usable_body", "not_surveyed"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain"})


@dataclass(frozen=True)
class SourceRecord:
    id: str
    place_id: str
    institution: str
    allowed_urls: tuple
    question_types: tuple
    parser: str
    last_success: str | None
    enabled: bool
    disabled_reason: str | None
    fixture: str | None
    survey_note: str
    redirect_hops: tuple


@dataclass(frozen=True)
class Registry:
    sources: tuple
    by_id: dict

    def get(self, source_id):
        try:
            return self.by_id[source_id]
        except KeyError:
            raise KeyError(source_id) from None

    @property
    def enabled(self):
        return tuple(source for source in self.sources if source.enabled)


def load_registry(root, catalog):
    root = Path(root)
    document = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    rows = document.get("sources")
    if not isinstance(rows, list) or not rows:
        raise PlacesError("sources.json needs a sources list")
    sources = []
    seen = set()
    for row in rows:
        source = source_from_mapping(row, root)
        if source.id in seen:
            raise PlacesError(f"duplicate source id {source.id}")
        seen.add(source.id)
        if source.place_id not in catalog.by_id:
            raise PlacesError(f"{source.id} place is not in the catalog")
        sources.append(source)
    by_place = {}
    for source in sources:
        by_place.setdefault(source.place_id, set()).add(source.id)
    for entry in catalog.entries:
        unknown = [source_id for source_id in entry.source_ids if source_id not in seen]
        if unknown:
            raise PlacesError(f"{entry.id} names unknown sources {unknown}")
        listed = by_place.get(entry.id, set())
        if set(entry.source_ids) != listed:
            raise PlacesError(f"{entry.id} sources do not match the registry")
        attribution = tuple(
            url for source in sources if source.place_id == entry.id for url in source.allowed_urls
        )
        if attribution != entry.source_urls:
            raise PlacesError(f"{entry.id} source URLs do not match the registry")
        for url in entry.source_urls:
            vet_url(url, (url,))
    return Registry(sources=tuple(sources), by_id={source.id: source for source in sources})


def source_from_mapping(row, root):
    required = {
        "id", "place_id", "institution", "allowed_urls", "question_types", "parser",
        "last_success", "enabled", "disabled_reason", "fixture", "survey_note",
    }
    if not isinstance(row, dict):
        raise PlacesError("source row must be an object")
    missing = sorted(required - set(row))
    extra = sorted(set(row) - required - {"redirect_hops"})
    if missing or extra:
        raise PlacesError(f"source fields missing={missing} extra={extra}")
    if row["parser"] != PARSER_ID:
        raise PlacesError(f"{row.get('id')} parser is not {PARSER_ID}")
    if not isinstance(row["enabled"], bool):
        raise PlacesError(f"{row.get('id')} enabled must be true or false")
    allowed = _url_tuple(row["allowed_urls"], row.get("id"))
    if len(allowed) > MAX_PAGES_PER_CHECK:
        raise PlacesError(f"{row.get('id')} lists more than {MAX_PAGES_PER_CHECK} pages")
    for url in allowed:
        vet_url(url, (url,))
        if urlsplit(url).scheme != "https":
            raise PlacesError(f"{row.get('id')} allowed URL must be https")
    question_types = row["question_types"]
    if not isinstance(question_types, list) or not question_types:
        raise PlacesError(f"{row.get('id')} question_types required")
    if not all(isinstance(item, str) and item.isidentifier() for item in question_types):
        raise PlacesError(f"{row.get('id')} question_types must be identifiers")
    last_success = row["last_success"]
    if last_success is not None:
        _timestamp(last_success, row.get("id"))
    fixture = row["fixture"]
    reason = row["disabled_reason"]
    hops = tuple(row.get("redirect_hops") or [])
    if not isinstance(row.get("survey_note"), str):
        raise PlacesError(f"{row.get('id')} survey_note required")
    if row["enabled"]:
        if reason is not None or not fixture or not allowed or hops:
            raise PlacesError(f"{row.get('id')} enabled sources need a fixture, URLs, and no disablement")
        path = Path(root) / "fixtures" / fixture
        if not path.is_file():
            raise PlacesError(f"{row.get('id')} fixture is missing")
    else:
        if reason not in _REASONS or fixture is not None or hops:
            raise PlacesError(f"{row.get('id')} disabled sources need a known reason and no fixture")
    if not str(row["id"]).startswith("src."):
        raise PlacesError("source id must start with src.")
    return SourceRecord(
        id=row["id"],
        place_id=row["place_id"],
        institution=row["institution"],
        allowed_urls=allowed,
        question_types=tuple(question_types),
        parser=row["parser"],
        last_success=last_success,
        enabled=row["enabled"],
        disabled_reason=reason,
        fixture=fixture,
        survey_note=row["survey_note"],
        redirect_hops=hops,
    )


def vet_url(url, allowed_urls):
    """Refuse anything that is not an exact allowed public http(s) URL."""
    if not isinstance(url, str) or not url or url.strip() != url:
        raise NavigationRefused("unregistered")
    if "\\" in url or any(ord(char) < 32 or ord(char) > 126 for char in url):
        raise NavigationRefused("unregistered")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise NavigationRefused("non_http")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise NavigationRefused("unregistered")
    host = parts.hostname
    if not host:
        raise NavigationRefused("unregistered")
    path = parts.path or "/"
    if any(piece == ".." for piece in path.split("/")) or "//" in path:
        raise NavigationRefused("unregistered")
    port = parts.port
    if scheme == "https" and port not in (None, 443):
        raise NavigationRefused("unregistered")
    if scheme == "http" and port not in (None, 80):
        raise NavigationRefused("unregistered")
    kind = classify_host(host)
    if kind != "public":
        raise NavigationRefused(kind)
    key = (scheme, host.lower().rstrip("."), path)
    allowed = set()
    for item in allowed_urls:
        item_parts = urlsplit(item)
        allowed.add((item_parts.scheme.lower(), (item_parts.hostname or "").lower().rstrip("."), item_parts.path or "/"))
    if key not in allowed:
        raise NavigationRefused("unregistered")
    return key


def classify_host(host):
    name = host.lower().rstrip(".")
    if name in _LOCAL_NAMES or name.endswith(".localhost") or name.endswith(".local") or name.endswith(".internal"):
        return "local"
    if re.fullmatch(r"[0-9.]+", name) and name.count(".") != 3:
        return "private"
    if re.fullmatch(r"\d+", name) or name.startswith("0x"):
        return "private"
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return "public"
    if address.version == 4 and address in _CGNAT:
        return "private"
    if (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
            or address.is_multicast or address.is_unspecified or not address.is_global):
        return "private"
    return "public"


def _url_tuple(values, source_id):
    if not isinstance(values, list):
        raise PlacesError(f"{source_id} allowed_urls must be a list")
    if not all(isinstance(item, str) and item for item in values):
        raise PlacesError(f"{source_id} allowed_urls must be URL strings")
    return tuple(values)


def _timestamp(value, source_id):
    if not isinstance(value, str):
        raise PlacesError(f"{source_id} last_success must be a timestamp or null")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise PlacesError(f"{source_id} last_success is not a timestamp") from None
    if parsed.tzinfo is None:
        raise PlacesError(f"{source_id} last_success must be timezone-aware")
