"""Place entries, aliases, and coverage-gap search. Offline only."""

import unittest
from pathlib import Path

from angeleno.places import PlacesError, load_bundle
from angeleno.places.catalog import parse_entry_text


ROOT = Path(__file__).resolve().parents[1] / "data" / "places"


def bundle():
    return load_bundle(ROOT)


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = bundle()
        cls.catalog = cls.bundle.catalog

    def test_core_circle_two_cultural_venues_and_secondary_nodes(self):
        ids = {entry.id for entry in self.catalog.entries}
        self.assertTrue({
            "place.jefferson-park", "place.usc", "place.exposition-park",
            "place.fisher", "place.nhm", "place.culver-city", "place.sherman-oaks",
        } <= ids)
        enabled = {source.place_id for source in self.bundle.registry.enabled if "hours" in source.question_types}
        self.assertTrue({"place.fisher", "place.nhm"} <= enabled)
        volatile = {entry.id for entry in self.catalog.entries if entry.volatile}
        self.assertEqual(volatile, {"place.fisher", "place.nhm"})
        for entry in self.catalog.entries:
            self.assertEqual(entry.checked, "2026-10-30")
            self.assertNotIn("12:00", entry.summary)
            self.assertNotIn("画框店", entry.summary.casefold())
            self.assertNotIn("frame shop", entry.summary.casefold())

    def test_aliases_match_entries_and_fictional_place_is_absent(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "entries").glob("*.md"))
        text += (ROOT / "aliases.json").read_text(encoding="utf-8")
        self.assertNotIn("画框店", text)
        self.assertNotIn("frame shop", text.casefold())
        fisher = self.catalog.by_id["place.fisher"]
        self.assertIn("费舍尔艺术博物馆", fisher.aliases)
        self.assertIn("USC Fisher", fisher.aliases)

    def test_unique_alias_prefers_the_longer_name(self):
        found = self.catalog.search("USC Fisher?")
        self.assertFalse(found.needs_clarification)
        self.assertEqual([hit.place_id for hit in found.hits], ["place.fisher"])
        self.assertEqual(found.hits[0].via, "alias")
        named = self.catalog.search("NHM")
        self.assertEqual([hit.place_id for hit in named.hits], ["place.nhm"])

    def test_shared_short_name_asks_instead_of_picking(self):
        found = self.catalog.search("博物馆")
        self.assertTrue(found.needs_clarification)
        self.assertFalse(found.coverage_gap)
        self.assertEqual(
            [hit.place_id for hit in found.hits],
            ["place.caam", "place.fisher", "place.nhm"],
        )
        mixed = self.catalog.search("Exposition Park 的自然历史博物馆")
        self.assertTrue(mixed.needs_clarification)
        self.assertEqual(
            {hit.place_id for hit in mixed.hits},
            {"place.exposition-park", "place.nhm"},
        )

    def test_gap_is_not_a_nonexistence_claim(self):
        found = self.catalog.search("画框店")
        self.assertTrue(found.coverage_gap)
        self.assertFalse(found.needs_clarification)
        self.assertEqual(found.hits, ())
        self.assertNotIn("exists", found.__dataclass_fields__)
        self.assertNotIn("existence", found.__dataclass_fields__)

    def test_summary_hit_and_area_filter(self):
        found = self.catalog.search("保护背景")
        self.assertEqual([hit.place_id for hit in found.hits], ["place.jefferson-park"])
        self.assertEqual(found.hits[0].via, "summary")
        narrowed = self.catalog.search("Fisher", area="jefferson-park")
        self.assertTrue(narrowed.coverage_gap)
        chosen = self.catalog.search("ignored", place_id="place.fisher")
        self.assertEqual([hit.place_id for hit in chosen.hits], ["place.fisher"])
        self.assertEqual(chosen.hits[0].via, "id")
        self.assertTrue(self.catalog.search("x", place_id="place.fisher", area="jefferson-park").coverage_gap)

    def test_unknown_id_and_empty_question_fail_closed(self):
        with self.assertRaises(KeyError):
            self.catalog.search("x", place_id="place.missing")
        with self.assertRaises(PlacesError):
            self.catalog.search("   ")
        with self.assertRaises(PlacesError):
            self.catalog.search("a" * 501)

    def test_entry_text_requires_the_frozen_fields(self):
        with self.assertRaises(PlacesError):
            parse_entry_text("---\nid: place.demo\n---\n\n正文\n")
        with self.assertRaises(PlacesError):
            parse_entry_text(
                "---\nid: place.demo\nname: Demo\naliases:\n  - Demo\narea: core\n"
                "themes:\n  - art\nvolatile: true\nchecked: 2026-13-01\nsources:\n"
                "  - src.demo\nsource_urls:\n---\n\n正文\n"
            )

    def test_search_does_not_read_fixtures(self):
        before = list(self.bundle.library.opened)
        self.catalog.search("Jefferson Park")
        self.assertEqual(self.bundle.library.opened, before)


if __name__ == "__main__":
    unittest.main()
