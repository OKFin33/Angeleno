"""Offline place catalog, registered sources, and result events.

Loading this package does not contact the network or a model. Voice runtime
wiring is intentionally absent.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .catalog import Catalog, SearchHit, SearchOutcome, load_catalog
from .check import Checker, external_read_exhausted
from .clock import FixedClock, WhenResolution, resolve_when
from .events import Card, PublishOutcome, ResultChannel
from .extract import extract_html
from .fixture import FixtureLibrary
from .model import (
    CHANNEL, LA_ZONE, MAX_BODY_BYTES, MAX_EXTERNAL_READ_SECONDS, MAX_EXTRACT_CHARS,
    MAX_PAGES_PER_CHECK, MAX_QUESTION_CHARS, PARSER_ID, STATUSES,
    Citation, NavigationRefused, PlacesError, QueryIntent, ResultEvent, SourceDisabled,
)
from .registry import Registry, SourceRecord, load_registry, vet_url


def default_root():
    return Path(__file__).resolve().parents[2] / "data" / "places"


@dataclass(frozen=True)
class PlaceBundle:
    root: Path
    catalog: Catalog
    registry: Registry
    library: FixtureLibrary


def load_bundle(root=None):
    root = Path(root) if root is not None else default_root()
    _verify_manifest(root)
    catalog = load_catalog(root)
    registry = load_registry(root, catalog)
    return PlaceBundle(root, catalog, registry, FixtureLibrary(root))


def _verify_manifest(root):
    path = root / "fixtures" / "manifest.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    files = document.get("fixtures")
    if not isinstance(files, dict) or not files:
        raise PlacesError("fixture manifest is empty")
    for name, digest in files.items():
        file_path = root / "fixtures" / name
        actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual != digest:
            raise PlacesError(f"fixture {name} does not match the frozen manifest")
    cases = document.get("redirect_cases")
    if not isinstance(cases, list) or not cases:
        raise PlacesError("fixture manifest needs redirect cases")


__all__ = [
    "CHANNEL", "LA_ZONE", "MAX_BODY_BYTES", "MAX_EXTERNAL_READ_SECONDS", "MAX_EXTRACT_CHARS",
    "MAX_PAGES_PER_CHECK", "MAX_QUESTION_CHARS", "PARSER_ID", "STATUSES",
    "Card", "Catalog", "Checker", "Citation", "FixedClock", "FixtureLibrary",
    "NavigationRefused", "PlaceBundle", "PlacesError", "PublishOutcome", "QueryIntent",
    "Registry", "ResultChannel", "ResultEvent", "SearchHit", "SearchOutcome",
    "SourceDisabled", "SourceRecord", "WhenResolution",
    "default_root", "external_read_exhausted", "extract_html", "load_bundle",
    "load_catalog", "load_registry", "resolve_when", "vet_url",
]
