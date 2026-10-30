"""Portable author input and local-date checks, using a public synthetic pack."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from her_agent.author import load_pack
from her_agent.memory import seed_digest

ROOT = Path(__file__).resolve().parents[1]


class AuthorPackTests(unittest.TestCase):
    def test_relocation_preserves_author_identity_and_seed(self):
        original = load_pack(ROOT / 'examples/demo/author-pack.json')
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for name in ('author-pack.json', 'character.md'):
                (target / name).write_bytes((ROOT / 'examples/demo' / name).read_bytes())
            relocated = load_pack(target / 'author-pack.json')
            self.assertEqual(original.id, relocated.id)
            self.assertEqual(seed_digest(original), seed_digest(relocated))
            self.assertEqual(original.messages([{'role': 'user', 'content': '聊聊附近。'}], today='2026-10-30'),
                             relocated.messages([{'role': 'user', 'content': '聊聊附近。'}], today='2026-10-30'))

    def test_la_date_and_authored_start_remain_distinct(self):
        pack = load_pack(ROOT / 'examples/demo/author-pack.json')
        with patch.dict(os.environ, {}, clear=True):
            text = pack.instructions(today='2026-11-01')
        self.assertIn('America/Los_Angeles', text)
        self.assertIn('2026-11-01', text)
        self.assertIn('2026-10-30', text)
        self.assertIn('周日', text)

    def test_explicit_timezone_changes_display_not_seed(self):
        pack = load_pack(ROOT / 'examples/demo/author-pack.json')
        before = seed_digest(pack)
        with patch.dict(os.environ, {'ANGELENO_TIMEZONE': 'Asia/Taipei'}):
            self.assertIn('Asia/Taipei', pack.instructions(today='2026-10-30'))
        self.assertEqual(seed_digest(pack), before)

    def test_manifest_cannot_read_outside_its_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'pack').mkdir()
            (root / 'outside.md').write_text('not part of this author package')
            manifest = {'format': 'her-author-pack/1', 'id': 'synthetic', 'as_of': '2026-10-30',
                        'documents': [{'path': '../outside.md', 'kind': 'character'}]}
            (root / 'pack/author-pack.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                load_pack(root / 'pack/author-pack.json')


if __name__ == '__main__':
    unittest.main()
