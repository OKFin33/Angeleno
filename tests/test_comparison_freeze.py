"""Pinned offline comparison controls. Not a score and not a paid run."""
import json
from pathlib import Path
import unittest

from angeleno.author import assistant_control_pack, load_pack
from angeleno.comparison import load_config, run_development
from angeleno.comparison_freeze import assess, build_checklist, checklist_path, difference
from angeleno.comparison_live import load_manifest

ROOT = Path(__file__).resolve().parents[1]


class FreezeChecklistTests(unittest.TestCase):
    def test_committed_checklist_matches_the_checkout(self):
        report = assess(ROOT)
        self.assertTrue(report["ok"], report)
        data = json.loads(checklist_path(ROOT).read_text())
        self.assertEqual(data, build_checklist(ROOT))
        self.assertEqual(data["status"], "offline_controls_frozen")
        self.assertEqual(data["product_candidate"]["commit"], "4185b8eb29e8fa62ddc32300cc9db526f6704e15")
        self.assertEqual(data["product_candidate"]["merged_pull_requests"], [30, 31, 32, 33])
        self.assertFalse(data["executable_live_manifest"])
        self.assertFalse(data["authorizes_paid_run"])
        self.assertIsNone(data["owner_calibration"])
        self.assertIsNone(data["preference_scores"])
        self.assertFalse(data["holdout_executed"])
        self.assertFalse(data["holdout_unseen"])
        self.assertEqual(data["model_parameters"]["model"], "deepseek-flash")
        self.assertEqual(data["model_parameters"]["language"], "zh")
        self.assertEqual(data["model_version_limitation"], "configured_alias_no_immutable_provider_revision")
        self.assertEqual(data["development_anchors"], ["DEV-D1", "DEV-D2", "DEV-D3"])
        self.assertEqual(data["holdout_proposal_ids"], ["H1", "H2", "H3", "H4", "H5"])
        self.assertEqual([item["id"] for item in data["open_gaps"]], [
            "non_grounding_persona_facts_not_copied",
            "historical_private_supplements_absent",
            "immutable_model_revision_unknown",
            "owner_calibration_absent",
            "unseen_holdout_absent",
            "real_model_comparison_not_run",
        ])
        for gap in data["open_gaps"]:
            self.assertEqual(gap["status"], "open")
            self.assertIsNone(gap["resolution"])
            self.assertTrue(gap["blocks_formal_comparison"])
        rubric = json.loads((ROOT / "examples/comparison/scoring-rubric.json").read_text())
        self.assertIsNone(rubric["owner_calibration"])
        self.assertIsNone(rubric["applied_scores"])
        self.assertIsNone(rubric["second_model_scores"])

    def test_live_loader_rejects_the_checklist(self):
        with self.assertRaises(ValueError):
            load_manifest(checklist_path(ROOT), ROOT / "examples/comparison/development.json")

    def test_tampered_grounding_hash_is_drift(self):
        data = json.loads(checklist_path(ROOT).read_text())
        data["file_sha256"]["characters/jo/persona/grounding.md"] = "0" * 64
        errors = difference(data, ROOT)
        self.assertEqual(len(errors), 1)
        self.assertIn("grounding.md", errors[0])


class PublicJoDryRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_jo_pack_completes_six_scripted_segments(self):
        config, anchors, corpus = load_config(ROOT / "examples/comparison/development.json")
        pack = load_pack(ROOT / "characters/jo/author-pack.json")
        report = await run_development(config, anchors, corpus, pack)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(report["segments"]), 6)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["network_calls"], 0)
        self.assertIsNone(report["owner_calibration"])
        self.assertFalse(report["ready_for_real_comparison"])
        self.assertFalse(report["holdout_included"])
        assistant = assistant_control_pack(pack)
        shared = lambda item: tuple(doc.sha256 for doc in item.documents if doc.kind == "grounding")
        self.assertEqual(shared(pack), shared(assistant))
        self.assertEqual(report["segments"][0]["web_calls"], report["segments"][1]["web_calls"])
