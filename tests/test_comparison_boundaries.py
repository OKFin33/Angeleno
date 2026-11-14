"""The E3–E8 and engineering-boundary map points at real tests or an explicit gap."""
import unittest
from pathlib import Path

from angeleno.comparison_boundaries import BOUNDARIES, NO_OFFLINE_TEST, boundary_ids, by_id, e7_verdict_allowed
from angeleno.comparison_freeze import assess

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "E3", "E4", "E5", "E6", "E7", "E8",
    "no_persist", "cross_identity", "untrusted_page", "quota_stop", "status_not_speech",
)


class BoundaryMapTests(unittest.TestCase):
    def test_every_required_boundary_is_present_once(self):
        self.assertEqual(boundary_ids(), REQUIRED)
        self.assertEqual(len(boundary_ids()), len(set(boundary_ids())))

    def test_each_entry_is_a_real_test_or_an_explicit_offline_gap(self):
        for item in BOUNDARIES:
            if item["status"] == NO_OFFLINE_TEST:
                self.assertEqual(item["tests"], ())
                self.assertTrue(item["reason"])
                self.assertTrue(item["mechanism_not_acceptance"])
            else:
                self.assertEqual(item["status"], "mapped")
                self.assertTrue(item["tests"])
                self.assertNotIn("mechanism_not_acceptance", item)
            for cited in (*item["tests"], *item.get("mechanism_not_acceptance", ())):
                line = (ROOT / cited["file"]).read_text().splitlines()[cited["line"] - 1]
                self.assertIn(f"def {cited['name']}(", line, cited)

    def test_e7_gap_is_recorded_and_is_not_a_pass(self):
        e7 = by_id("E7")
        self.assertEqual(e7["status"], NO_OFFLINE_TEST)
        self.assertIn("docs/spec.md:203", e7["reason"])
        self.assertIn("docs/trial.md:105", e7["reason"])
        self.assertFalse(e7_verdict_allowed("pass", None))
        self.assertFalse(e7_verdict_allowed("insufficient", None))
        self.assertFalse(e7_verdict_allowed(NO_OFFLINE_TEST, None))
        self.assertTrue(e7_verdict_allowed("fail", None))
        self.assertFalse(e7_verdict_allowed("pass", {"kind": "offline", "note": "合成会话"}))
        self.assertTrue(e7_verdict_allowed("pass", {"kind": "device", "note": "实机听过未播完后的重拨"}))

    def test_adding_the_map_does_not_move_the_freeze_fingerprint(self):
        report = assess(ROOT)
        self.assertTrue(report["ok"], report)
