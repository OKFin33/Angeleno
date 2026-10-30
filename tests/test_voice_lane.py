"""Offline voice-lane record checks. No audio, network, devices, or env files."""

import ast
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import voice_lane


ROOT = Path(__file__).resolve().parents[1]
NOTES = ("private", "voice-evidence", "run-1", "notes.txt")
LICENCE_NOTES = ("private", "voice-licence", "record.json")


def latency(**overrides):
    marks = {
        "user_speech_end": 0,
        "asr_confirmed": 400,
        "model_first_token": 900,
        "tts_first_audio": 1500,
        "playback_audible": 1700,
    }
    document = {
        "sample_count": 1,
        "claims_stable_percentile": False,
        "human_heard": True,
        "answer_audio_not_filler": True,
        "marks_ms": marks,
        "durations_ms": {
            "asr": 400,
            "model": 500,
            "tts_first_audio": 600,
            "playback": 200,
        },
        "signals": dict(voice_lane.SIGNALS),
    }
    document.update(overrides)
    return document


def passed(root, basis):
    return {
        "result": "pass",
        "basis": basis,
        "heard": "heard on a headset in the local browser",
        "evidence_path": str(root.joinpath(*NOTES)),
    }


def all_passed(root):
    return {name: passed(root, basis) for name, basis in voice_lane.ITEM_BASIS.items()}


def licence(root, **overrides):
    document = {
        "schema": voice_lane.LICENCE_SCHEMA,
        "asset_kind": "vendor_voice",
        "covers": ["private_test", "public_demo", "published_recording", "generated_speech"],
        "cloning_explicit": False,
        "model_weight_licence_noted": True,
        "record_path": str(root.joinpath(*LICENCE_NOTES)),
    }
    document.update(overrides)
    return document


def sheet(root, **overrides):
    document = {
        "schema": voice_lane.SCHEMA,
        "voice_slot": "private_reference",
        "public_artifact": False,
        "public_uses": [],
        "licence": None,
        "items": {name: {"result": "not_run"} for name in voice_lane.REQUIRED_ITEMS},
        "latency": None,
    }
    document.update(overrides)
    return document


class VoiceLaneTests(unittest.TestCase):
    def test_blank_sheet_is_well_formed_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(voice_lane.validate_voice_lane(voice_lane.blank_document(), repo_root=root), [])
            self.assertFalse((root / "private").exists())

    def test_module_does_not_import_network_or_process_libraries(self):
        tree = ast.parse((ROOT / "scripts" / "voice_lane.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertTrue(imported.isdisjoint({"socket", "urllib", "http", "aiohttp", "subprocess", "ssl"}))

    def test_private_pass_sheet_keeps_evidence_path_unopened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = sheet(
                root,
                items=all_passed(root),
                latency=latency(),
            )
            evidence = root.joinpath(*NOTES)
            self.assertEqual(voice_lane.validate_voice_lane(document, repo_root=root), [])
            self.assertFalse(evidence.exists())
            self.assertFalse(root.joinpath("private").exists())

    def test_public_artifact_from_private_reference_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = sheet(
                root,
                public_artifact=True,
                public_uses=["public_demo", "published_recording", "generated_speech"],
                licence=licence(root),
                items=all_passed(root),
                latency=latency(),
            )
            errors = voice_lane.validate_voice_lane(document, repo_root=root)
            self.assertIn("public artifact is blocked while voice_slot is private_reference", errors)

    def test_public_replacement_requires_named_covers_and_explicit_cloning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = dict(
                voice_slot="replacement",
                public_artifact=True,
                public_uses=["public_demo", "generated_speech"],
                items=all_passed(root),
                latency=latency(),
            )
            missing = sheet(root, licence=licence(root, covers=["public_demo"]), **base)
            self.assertIn("public use is not covered: generated_speech", voice_lane.validate_voice_lane(missing, repo_root=root))
            cloned = sheet(
                root,
                licence=licence(root, asset_kind="reference_clip", cloning_explicit=False),
                **base,
            )
            self.assertIn(
                "reference_clip public use requires cloning_explicit true",
                voice_lane.validate_voice_lane(cloned, repo_root=root),
            )
            accepted = sheet(root, licence=licence(root, asset_kind="reference_clip", cloning_explicit=True), **base)
            self.assertEqual(voice_lane.validate_voice_lane(accepted, repo_root=root), [])

    def test_evidence_paths_stay_outside_tracked_tree_and_env_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = Path(tempfile.gettempdir()) / "angeleno-voice-evidence-outside.txt"
            tracked = sheet(root, items={"place_names": {**passed(root, "heard_place"), "evidence_path": str(root / "docs" / "notes.txt")}})
            self.assertIn(
                "item place_names evidence path inside the repository must stay under private/",
                voice_lane.validate_voice_lane(tracked, repo_root=root),
            )
            relative = sheet(root, items={"place_names": {**passed(root, "heard_place"), "evidence_path": "private/voice-evidence/notes.txt"}})
            self.assertIn(
                "item place_names evidence path must be an absolute path",
                voice_lane.validate_voice_lane(relative, repo_root=root),
            )
            env_file = sheet(root, items={"place_names": {**passed(root, "heard_place"), "evidence_path": str(root / "private" / ".env")}})
            self.assertIn(
                "item place_names evidence path must not be an env file",
                voice_lane.validate_voice_lane(env_file, repo_root=root),
            )
            external = sheet(root, items={"place_names": {**passed(root, "heard_place"), "evidence_path": str(outside)}})
            external["items"].update({name: {"result": "not_run"} for name in voice_lane.REQUIRED_ITEMS if name != "place_names"})
            self.assertEqual(voice_lane.validate_voice_lane(external, repo_root=root), [])
            self.assertFalse(outside.exists())

    def test_latency_rejects_reordered_marks_mismatched_durations_and_substitutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reordered = latency()
            reordered["marks_ms"] = {**reordered["marks_ms"], "model_first_token": 100}
            self.assertTrue(any("earlier than the previous mark" in error for error in voice_lane.validate_voice_lane(sheet(root, latency=reordered), repo_root=root)))
            mismatch = latency()
            mismatch["durations_ms"] = {**mismatch["durations_ms"], "asr": 1}
            self.assertIn(
                "durations_ms.asr must equal asr_confirmed minus user_speech_end",
                voice_lane.validate_voice_lane(sheet(root, latency=mismatch), repo_root=root),
            )
            substitute = latency()
            substitute["signals"] = {**substitute["signals"], "playback": "subtitle"}
            self.assertIn(
                "signals must match the voice-lane stage signals",
                voice_lane.validate_voice_lane(sheet(root, latency=substitute), repo_root=root),
            )
            percentile = latency(claims_stable_percentile=True)
            self.assertIn(
                "claims_stable_percentile must be false",
                voice_lane.validate_voice_lane(sheet(root, latency=percentile), repo_root=root),
            )

    def test_stage_pass_cannot_use_filler_or_unheard_playback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = {name: {"result": "not_run"} for name in voice_lane.REQUIRED_ITEMS}
            items["latency_tts_first_audio"] = passed(root, "stage_clock")
            items["latency_playback"] = passed(root, "stage_clock")
            items["playback"] = passed(root, "human_heard")
            filler = sheet(root, items=items, latency=latency(answer_audio_not_filler=False, human_heard=False))
            errors = voice_lane.validate_voice_lane(filler, repo_root=root)
            self.assertIn("latency_tts_first_audio pass requires answer_audio_not_filler true", errors)
            self.assertIn("latency_playback pass requires human_heard true", errors)
            self.assertIn("playback pass contradicts latency.human_heard false", errors)
            recorded_failure = sheet(
                root,
                items={**{name: {"result": "not_run"} for name in voice_lane.REQUIRED_ITEMS},
                       "latency_tts_first_audio": {"result": "fail", "reason": "only the lookup status was audible"}},
                latency=latency(answer_audio_not_filler=False),
            )
            self.assertEqual(voice_lane.validate_voice_lane(recorded_failure, repo_root=root), [])

    def test_optional_marks_must_move_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backwards = latency(optional_marks_ms={"query_start": 800, "query_end": 700, "interruption": 900, "audio_stop": 1000})
            errors = voice_lane.validate_voice_lane(sheet(root, latency=backwards), repo_root=root)
            self.assertIn("query_end is earlier than query_start", errors)
            forward = latency(optional_marks_ms={"query_start": 700, "query_end": 800})
            self.assertEqual(voice_lane.validate_voice_lane(sheet(root, latency=forward), repo_root=root), [])

    def test_wrong_basis_secret_text_and_missing_item_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrong = sheet(root, items={"place_names": passed(root, "subtitle")})
            self.assertIn("item place_names pass requires basis heard_place", voice_lane.validate_voice_lane(wrong, repo_root=root))
            leaked = sheet(root)
            leaked["items"]["place_names"] = {"result": "fail", "reason": "DASHSCOPE_API_KEY=synthetic"}
            self.assertIn("document contains a credential-like value", voice_lane.validate_voice_lane(leaked, repo_root=root))
            reference = sheet(root)
            reference["items"]["playback"] = {"result": "fail", "reason": "VOXCPM_REFERENCE=/tmp/clip.wav"}
            self.assertIn("document contains a credential-like value", voice_lane.validate_voice_lane(reference, repo_root=root))
            missing = sheet(root)
            del missing["items"]["barge_in"]
            self.assertIn("missing checklist item: barge_in", voice_lane.validate_voice_lane(missing, repo_root=root))

    def test_cli_blank_and_check_do_not_follow_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                blank_code = voice_lane.main(["blank"])
            self.assertEqual(blank_code, 0)
            self.assertEqual(voice_lane.validate_voice_lane(json.loads(stdout.getvalue()), repo_root=root), [])
            path = root / "checklist.json"
            path.write_text(stdout.getvalue(), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = voice_lane.main(["check", str(path), "--repo-root", str(root)])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stdout.getvalue())["well_formed"])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = voice_lane.main(["check", str(root / "missing.json")])
            self.assertEqual(code, 1)
            self.assertFalse(stdout.getvalue())
            self.assertIn("No such file", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
