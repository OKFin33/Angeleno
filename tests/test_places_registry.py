"""Source registry, frozen fixtures, and URL refusal. No live fetch."""

import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from her_agent.places import (
    MAX_EXTERNAL_READ_SECONDS, MAX_PAGES_PER_CHECK, NavigationRefused, PlacesError,
    external_read_exhausted, load_bundle, vet_url,
)
from her_agent.places.registry import source_from_mapping


ROOT = Path(__file__).resolve().parents[1] / "data" / "places"
PACKAGE = Path(__file__).resolve().parents[1] / "her_agent" / "places"


def bundle():
    return load_bundle(ROOT)


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = bundle()

    def test_enabled_sources_are_frozen_and_disabled_sources_have_no_fixture(self):
        reasons = {}
        for source in self.bundle.registry.sources:
            self.assertIsNone(source.last_success)
            self.assertEqual(source.parser, "places.extract.v1")
            reasons[source.id] = source.disabled_reason
            if source.enabled:
                self.assertTrue((ROOT / "fixtures" / source.fixture).is_file())
                self.assertGreaterEqual(len(source.allowed_urls), 1)
            else:
                self.assertIsNone(source.fixture)
        self.assertEqual(reasons["src.jefferson.planning"], "timeout")
        self.assertEqual(reasons["src.exposition.official"], "http_403")
        self.assertEqual(reasons["src.caam.visit"], "no_usable_body")
        self.assertEqual(reasons["src.culver.identity"], "not_surveyed")
        self.assertEqual(
            self.bundle.registry.get("src.culver.identity").allowed_urls,
            ("https://www.culvercity.org/",),
        )
        self.assertEqual(self.bundle.registry.get("src.sherman.orientation").allowed_urls, ())
        names = {path.name for path in (ROOT / "fixtures").iterdir()}
        self.assertEqual(names, {
            "fisher-visit.html", "nhm-visit.html", "jefferson-overview.html",
            "private-trap.html", "manifest.json",
        })

    def test_manifest_hashes_and_redirect_cases(self):
        document = json.loads((ROOT / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
        for name, digest in document["fixtures"].items():
            actual = hashlib.sha256((ROOT / "fixtures" / name).read_bytes()).hexdigest()
            self.assertEqual(actual, digest)
        allowed = self.bundle.registry.get("src.fisher.visit").allowed_urls
        seen = set()
        for case in document["redirect_cases"]:
            seen.add(case["id"])
            if case["reason"] is None:
                vet_url(case["url"], allowed)
            else:
                with self.subTest(case=case["id"]):
                    with self.assertRaises(NavigationRefused) as caught:
                        vet_url(case["url"], allowed)
                    self.assertEqual(caught.exception.reason, case["reason"])
        self.assertIn("link-local", seen)
        self.assertIn("ipv6-loop", seen)
        self.assertIn("decimal-loopback", seen)

    def test_private_allowlist_and_too_many_pages_are_rejected(self):
        row = _row(allowed_urls=["https://127.0.0.1/secret"], enabled=True, fixture="fisher-visit.html")
        with self.assertRaises(NavigationRefused):
            source_from_mapping(row, ROOT)
        wide = _row(allowed_urls=["https://fisher.usc.edu/visit-us/"] * (MAX_PAGES_PER_CHECK + 1))
        with self.assertRaises(PlacesError):
            source_from_mapping(wide, ROOT)

    def test_malicious_hops_do_not_open_the_trap(self):
        library = self.bundle.library
        source = self.bundle.registry.get("src.fisher.visit")
        before = list(library.opened)
        with self.assertRaises(NavigationRefused):
            library.read(source, hops=("https://fisher.usc.edu/visit-us/", "http://127.0.0.1/admin"))
        with self.assertRaises(NavigationRefused):
            library.read(source, hops=("http://10.0.0.1/secret",))
        with self.assertRaises(PlacesError):
            library.read(source, hops=("https://fisher.usc.edu/visit-us/",) * 3)
        self.assertEqual(library.opened, before)
        self.assertIn("SECRET_PRIVATE_BODY", (ROOT / "fixtures" / "private-trap.html").read_text(encoding="utf-8"))

    def test_external_read_budget_is_ten_seconds(self):
        self.assertEqual(MAX_EXTERNAL_READ_SECONDS, 10)
        self.assertFalse(external_read_exhausted(9.9))
        self.assertTrue(external_read_exhausted(10))
        with self.assertRaises(PlacesError):
            external_read_exhausted(-1)

    def test_package_has_no_network_client(self):
        banned = (
            "import httpx", "from httpx", "import requests", "import aiohttp",
            "import socket", "urllib.request", "http.client", "urlopen", "datetime.now",
        )
        for path in PACKAGE.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for name in banned:
                self.assertNotIn(name, text, path.name)

    def test_load_and_vet_do_not_open_sockets(self):
        def fail(*_args, **_kwargs):
            raise AssertionError("network")
        with patch("socket.socket", fail), patch("socket.create_connection", fail), patch("socket.getaddrinfo", fail):
            loaded = load_bundle(ROOT)
            vet_url("https://nhm.org/plan-your-visit", loaded.registry.get("src.nhm.visit").allowed_urls)
        self.assertIsNotNone(loaded.registry.get("src.nhm.visit"))


def _row(**overrides):
    row = {
        "id": "src.demo.visit",
        "place_id": "place.fisher",
        "institution": "Demo",
        "allowed_urls": ["https://fisher.usc.edu/visit-us/"],
        "question_types": ["hours"],
        "parser": "places.extract.v1",
        "last_success": None,
        "enabled": False,
        "disabled_reason": "not_surveyed",
        "fixture": None,
        "survey_note": "test row",
    }
    row.update(overrides)
    return row


if __name__ == "__main__":
    unittest.main()
