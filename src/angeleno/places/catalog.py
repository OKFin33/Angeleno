"""Markdown place entries and alias search.

A miss is a coverage gap. The outcome has no field that claims a place does
not exist. Several alias hits stay unresolved instead of picking one.
"""

from dataclasses import dataclass
from datetime import date
import json
import re
from pathlib import Path

from .model import MAX_QUESTION_CHARS, PlacesError


_PUNCT = re.compile(r"[\"“”‘'.,!?？！，。、；;:：()（）\[\]{}]")


@dataclass(frozen=True)
class PlaceEntry:
    id: str
    name: str
    aliases: tuple
    area: str
    themes: tuple
    summary: str
    source_ids: tuple
    source_urls: tuple
    volatile: bool
    checked: str


@dataclass(frozen=True)
class SearchHit:
    place_id: str
    name: str
    aliases: tuple
    area: str
    themes: tuple
    summary: str
    source_ids: tuple
    source_urls: tuple
    volatile: bool
    checked: str
    matched_on: str | None
    via: str


@dataclass(frozen=True)
class SearchOutcome:
    hits: tuple
    coverage_gap: bool
    needs_clarification: bool


@dataclass(frozen=True)
class Catalog:
    entries: tuple
    by_id: dict

    def search(self, question, *, place_id=None, area=None):
        if place_id is not None:
            if not isinstance(place_id, str) or not place_id:
                raise PlacesError("place_id required")
            entry = self.by_id.get(place_id)
            if entry is None:
                raise KeyError(place_id)
            if area is not None and entry.area != area:
                return _gap()
            return _selected((entry,), {entry.id: (entry.name, "id")})
        if not isinstance(question, str) or not question.strip():
            raise PlacesError("question required")
        if len(question) > MAX_QUESTION_CHARS:
            raise PlacesError("question too long")
        pool = [entry for entry in self.entries if area is None or entry.area == area]
        query = normalize(question)
        matched = []
        for entry in pool:
            alias = _best_alias(entry, query)
            if alias is not None:
                matched.append((entry, alias))
        if matched:
            kept = _prefer_longer(matched)
            via = {entry.id: (alias, "alias") for entry, alias in kept}
            return _selected(tuple(entry for entry, _alias in kept), via)
        summarized = []
        for entry in pool:
            if _summary_hit(normalize(entry.summary), query):
                summarized.append(entry)
        if not summarized:
            return _gap()
        via = {entry.id: (None, "summary") for entry in summarized}
        return _selected(tuple(summarized), via)


def load_catalog(root):
    root = Path(root)
    folder = root / "entries"
    paths = sorted(folder.glob("*.md"))
    if not paths:
        raise PlacesError("place catalog is empty")
    entries = []
    seen = set()
    for path in paths:
        entry = parse_entry_text(path.read_text(encoding="utf-8"), origin=str(path.name))
        if entry.id in seen:
            raise PlacesError(f"duplicate place id {entry.id}")
        seen.add(entry.id)
        entries.append(entry)
    _check_alias_file(root, entries)
    return Catalog(entries=tuple(entries), by_id={entry.id: entry for entry in entries})


def parse_entry_text(text, *, origin="entry"):
    raw, body = _front_matter(text, origin)
    required = ("id", "name", "aliases", "area", "themes", "volatile", "checked", "sources", "source_urls")
    missing = [key for key in required if key not in raw]
    if missing:
        raise PlacesError(f"{origin} missing {', '.join(missing)}")
    if not isinstance(raw["volatile"], bool):
        raise PlacesError(f"{origin} volatile must be true or false")
    try:
        date.fromisoformat(raw["checked"])
    except (TypeError, ValueError):
        raise PlacesError(f"{origin} checked must be YYYY-MM-DD") from None
    aliases = _strings(raw["aliases"], origin, "aliases")
    themes = _strings(raw["themes"], origin, "themes")
    source_ids = _strings(raw["sources"], origin, "sources")
    source_urls = _strings(raw["source_urls"], origin, "source_urls", allow_empty=True)
    place_id = raw["id"]
    name = raw["name"]
    if not isinstance(place_id, str) or not place_id.startswith("place."):
        raise PlacesError(f"{origin} id must start with place.")
    if not isinstance(name, str) or not name.strip():
        raise PlacesError(f"{origin} name required")
    if not isinstance(raw["area"], str) or not raw["area"]:
        raise PlacesError(f"{origin} area required")
    if not body:
        raise PlacesError(f"{origin} summary required")
    if len(set(aliases)) != len(aliases):
        raise PlacesError(f"{origin} repeated alias")
    return PlaceEntry(
        id=place_id,
        name=name.strip(),
        aliases=tuple(aliases),
        area=raw["area"],
        themes=tuple(themes),
        summary=body,
        source_ids=tuple(source_ids),
        source_urls=tuple(source_urls),
        volatile=raw["volatile"],
        checked=raw["checked"],
    )


def normalize(text):
    text = text.casefold().replace("’", "'")
    text = _PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _gap():
    return SearchOutcome(hits=(), coverage_gap=True, needs_clarification=False)


def _selected(entries, via):
    hits = []
    for entry in sorted(entries, key=lambda item: item.id):
        matched_on, kind = via[entry.id]
        hits.append(SearchHit(
            place_id=entry.id,
            name=entry.name,
            aliases=entry.aliases,
            area=entry.area,
            themes=entry.themes,
            summary=entry.summary,
            source_ids=entry.source_ids,
            source_urls=entry.source_urls,
            volatile=entry.volatile,
            checked=entry.checked,
            matched_on=matched_on,
            via=kind,
        ))
    return SearchOutcome(
        hits=tuple(hits),
        coverage_gap=False,
        needs_clarification=len(hits) > 1,
    )


def _best_alias(entry, query):
    best = None
    for alias in (entry.name, *entry.aliases):
        folded = normalize(alias)
        if folded and _alias_hit(folded, query) and (best is None or len(folded) > len(best)):
            best = folded
    return best


def _prefer_longer(matched):
    """Drop a shorter alias when it is only a phrase inside a longer hit."""
    kept = []
    for entry, alias in matched:
        shadowed = False
        for other_entry, other_alias in matched:
            if other_entry.id == entry.id:
                continue
            if len(other_alias) > len(alias) and _phrase_in(other_alias, alias):
                shadowed = True
                break
        if not shadowed:
            kept.append((entry, alias))
    return kept


def _alias_hit(alias, query):
    if _phrase_in(query, alias):
        return True
    if _cjk(query):
        return len(query) >= 2 and query in alias
    return len(query) >= 4 and _phrase_in(alias, query)


def _summary_hit(summary, query):
    if _cjk(query):
        return len(query) >= 2 and query in summary
    return len(query) >= 4 and _phrase_in(summary, query)


def _phrase_in(haystack, needle):
    if not needle:
        return False
    if _cjk(needle):
        return needle in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def _cjk(text):
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _front_matter(text, origin):
    if not text.startswith("---\n"):
        raise PlacesError(f"{origin} missing front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise PlacesError(f"{origin} unterminated front matter")
    data = {}
    current = None
    for line_no, line in enumerate(text[4:end].splitlines(), start=2):
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current is None:
                raise PlacesError(f"{origin}:{line_no} list item without a key")
            data[current].append(line[4:].strip())
            continue
        if line[:1].isspace() or ":" not in line:
            raise PlacesError(f"{origin}:{line_no} bad front matter")
        key, value = line.split(":", 1)
        value = value.strip()
        current = None
        if value == "":
            current = key
            data[key] = []
        elif value == "true":
            data[key] = True
        elif value == "false":
            data[key] = False
        else:
            data[key] = value
    return data, text[end + 5:].strip()


def _strings(values, origin, key, *, allow_empty=False):
    if not isinstance(values, list) or (not values and not allow_empty):
        raise PlacesError(f"{origin} {key} required")
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise PlacesError(f"{origin} {key} must be text")
    return [item.strip() for item in values]


def _check_alias_file(root, entries):
    path = Path(root) / "aliases.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document.get("aliases")
    if not isinstance(rows, list) or not rows:
        raise PlacesError("aliases.json needs an aliases list")
    expected = {}
    for entry in entries:
        for alias in entry.aliases:
            folded = normalize(alias)
            if folded in expected:
                raise PlacesError(f"alias {alias} is already used")
            expected[folded] = (alias, entry.id)
    seen = {}
    for row in rows:
        if set(row) != {"alias", "place_id"}:
            raise PlacesError("alias row must contain alias and place_id")
        alias = row["alias"]
        folded = normalize(alias)
        if folded in seen:
            raise PlacesError(f"duplicate alias {alias}")
        seen[folded] = (alias, row["place_id"])
    if seen != expected:
        missing = sorted(set(expected) - set(seen))
        extra = sorted(set(seen) - set(expected))
        mismatched = sorted(
            key for key in set(expected) & set(seen)
            if expected[key][1] != seen[key][1]
        )
        raise PlacesError(
            "aliases.json does not match entries"
            f" missing={missing} extra={extra} mismatched={mismatched}"
        )
