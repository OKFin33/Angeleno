"""Keep local data out of tracked sources while preserving public fixtures."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def load_gate():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_gate.py"
    spec = importlib.util.spec_from_file_location("verify_gate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class VerifyGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def test_bad_fixtures_fail_and_good_fixture_passes(self):
        gate = self.gate
        self.assertTrue(gate.BAD_FIXTURES)
        for fixture in gate.BAD_FIXTURES:
            self.assertTrue(gate.forbidden(gate.parse_paths(fixture)), fixture)
        self.assertFalse(gate.forbidden(gate.parse_paths(gate.GOOD_FIXTURE)))

    def test_private_tree_and_dotenv_are_forbidden(self):
        gate = self.gate
        self.assertEqual(gate.forbidden(["private"]), ["private"])
        self.assertEqual(gate.forbidden(["private/state.sqlite"]), ["private/state.sqlite"])
        self.assertEqual(gate.forbidden(["nested/.env"]), ["nested/.env"])
        self.assertEqual(gate.forbidden([".env.example"]), [])
        self.assertEqual(gate.forbidden(["privateer/readme.md"]), [])
        self.assertEqual(gate.forbidden(["docs/baseline.md", ".environment"]), [])

    def test_runtime_media_and_env_variants_fail_in_any_location(self):
        paths = [".scratch/run/result.json", "private", "evidence/raw/log.json",
                 ".env.production", "worker/.env.local", "worker/.env.example",
                 "data/memory.db", "data/memory.sqlite", "assets/budget.sqlite3",
                 "data/memory.db-wal", "data/memory.sqlite3-shm", "data/memory.sqlite-journal",
                 "assets/reference.WAV", "notes/voice.mp3", "clips/reference.flac",
                 "clips/recording.m4a", "clips/reference.mp4", "clips/input.webm"]
        self.assertEqual(self.gate.forbidden(paths), paths)

    def test_existing_synthetic_and_document_assets_remain_allowed(self):
        paths = [".env.example", "tests/fixtures/transient-preference-maintenance.json",
                 "data/places/fixtures/private-trap.html", "examples/comparison/corpus.synthetic.json",
                 "docs/screenshot.png", "web/livekit-client.umd.js", "uv.lock"]
        self.assertEqual(self.gate.forbidden(paths), [])

    def test_synthetic_audio_needs_exact_explicit_test_exception(self):
        clip = "tests/fixtures/synthetic-media/tone.wav"
        other = "tests/fixtures/synthetic-media/recording.wav"
        self.assertEqual(self.gate.forbidden([clip, other]), [clip, other])
        self.assertEqual(self.gate.forbidden([clip, other], synthetic_media_allowlist={clip}), [other])
        for bad in ["assets/reference.wav", "tests/fixtures/synthetic-media/../voice.wav",
                    "tests/fixtures/synthetic-media/", "tests/fixtures/synthetic-media/key.json"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.gate.forbidden([], synthetic_media_allowlist={bad})
        # A media exception must not bypass another protected category.
        self.assertEqual(self.gate.forbidden([".env.local", "private/reference.wav"],
                                            synthetic_media_allowlist={clip}),
                         [".env.local", "private/reference.wav"])

    def test_ci_has_no_media_path_filter_and_shared_ignore_covers_scratch(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/ci.yml").read_text()
        self.assertNotIn("paths-ignore:", workflow)
        self.assertIn("/.scratch/", (root / ".gitignore").read_text().splitlines())

    def test_self_check_fails_closed_on_injected_paths(self):
        gate = self.gate
        self.assertEqual(gate.self_check(["README.md", ".env.example"]), 0)
        self.assertEqual(gate.self_check(["private/ops.md"]), 1)
        self.assertEqual(gate.self_check(["pkg/.env"]), 1)

    def test_fixture_file_must_fail_when_bad(self):
        gate = self.gate
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.txt"
            good = Path(tmp) / "good.txt"
            bad.write_text(".env\nREADME.md\n", encoding="utf-8")
            good.write_text(".env.example\n", encoding="utf-8")
            self.assertNotEqual(gate.main(["--fixture", str(bad)]), 0)
            self.assertEqual(gate.main(["--fixture", str(good)]), 0)

    def test_self_check_uses_the_git_listing(self):
        gate = self.gate
        original = gate.git_ls_files

        def tracked_env(root=None):
            return ["README.md", ".env"]

        gate.git_ls_files = tracked_env
        try:
            self.assertEqual(gate.self_check(), 1)
        finally:
            gate.git_ls_files = original

    def test_current_tree_has_no_tracked_private_or_env(self):
        tracked = self.gate.git_ls_files()
        self.assertFalse(self.gate.forbidden(tracked))
        self.assertEqual(self.gate.self_check(tracked), 0)
        self.assertEqual(self.gate.self_check(), 0)
