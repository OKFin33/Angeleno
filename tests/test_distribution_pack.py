"""Public Jo defaults must work without an operator's private author package."""
from pathlib import Path
import shutil
import tempfile
import unittest
from her_agent.author import load_pack
from her_agent.baseline import configured_pack
from her_agent.config import load_env
from her_agent.voice import voice_configuration
from worker.reference import expected_jo_reference

ROOT=Path(__file__).resolve().parents[1]

class DistributionTests(unittest.TestCase):
    def test_default_jo_pack_is_self_contained_and_usable_outside_original_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(ROOT/'characters',root/'characters')
            pack=configured_pack({},root=root)
            self.assertEqual(pack.id,'jo')
            self.assertEqual(len(pack.documents),4)
            self.assertFalse((root/'private').exists())
            self.assertEqual(pack.instructions(today='2026-11-02'),load_pack(ROOT/'characters/jo/author-pack.json').instructions(today='2026-11-02'))
    def test_template_selects_jo_real_user_and_requires_own_reference_declaration(self):
        env=load_env(environ={},env_file=ROOT/'.env.example',root=ROOT)
        self.assertEqual(voice_configuration(env,root=ROOT)[0].id,'jo')
        self.assertEqual(env['ANGELENO_MEMORY_PURPOSE'],'real_user')
        self.assertEqual(env['ANGELENO_PAID_ENABLED'],'0')
        self.assertEqual(env['VOXCPM_REFERENCE'],'private/voices/reference.wav')
        with self.assertRaises(ValueError):expected_jo_reference(env)
