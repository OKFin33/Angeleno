"""Shared mechanics with explicit character/assistant roles; no model calls."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from her_agent.author import (ASSISTANT_SETTING, CAPABILITIES, MEMORY_CAPABILITIES,
    Document, assistant_control_pack, load_pack)
from her_agent.memory import seed_digest
from her_agent.runtime import ConversationRuntime, open_store
from her_agent.web_lookup import NOTE, WebLookup
from test_lookup import PACK, CLOCK, Scripted, IdleWriter
from test_web_lookup import FakeWeb


class RoleConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_existing_character_prompt_and_seed_are_unchanged(self):
        with patch.dict('os.environ', {'ANGELENO_TIMEZONE': 'America/Los_Angeles'}):
            prompt = PACK.instructions(today='2026-10-30')
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(),
                         'beb4e182a0af42b31b77ffaa137a183d0c5668b98747b070e0ebfb29ddeeae1c')
        self.assertEqual(seed_digest(PACK), 'f345d56d1eafe764323fdbf36c0d1b690cac482023e5117ba14f5dbfddeeb729')
        self.assertEqual(PACK.role, 'character')

    def test_assistant_role_keeps_grounding_without_persona_or_rehearsals(self):
        fact = Document('shared-grounding', 'grounding', 'synthetic', 'SAME FACT: hours unknown.', '')
        character = replace(PACK, documents=(*PACK.documents, fact))
        original = character.provenance()
        assistant = assistant_control_pack(character)
        self.assertIs(assistant.documents[-1], fact)
        self.assertEqual(character.provenance(), original)
        self.assertEqual(assistant.role, 'assistant')
        for memory in (False, True):
            prompt = assistant.instructions(today='2026-10-30', memory_enabled=memory)
            self.assertIn(ASSISTANT_SETTING, prompt)
            self.assertIn(fact.body, prompt)
            self.assertNotIn(PACK.documents[0].body, prompt)
            self.assertNotIn('自然扮演作者定义', prompt)
            self.assertNotIn('在作者允许的范围内可创作', prompt)
            self.assertIn(MEMORY_CAPABILITIES if memory else CAPABILITIES, prompt)
        messages = assistant.messages([{'role': 'user', 'content': '聊聊。'}],
                                       today='2026-10-30', memory_context={})
        self.assertNotIn('按角色虚构续接', messages[0]['content'])
        self.assertIn('不创造人物近况与往事', messages[0]['content'])
        # The same tool capability text does not reintroduce a persona mandate.
        for pack in (character, assistant):
            compiled = WebLookup.prepare_messages(pack.messages([{'role':'user','content':'聊聊'}], today='2026-10-30'))
            self.assertIn(NOTE, compiled[0]['content'])
            self.assertNotIn('以人物已定性格', NOTE)

    def test_role_change_cannot_reuse_character_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            open_store(root, PACK, 'control', create=True).close()
            with self.assertRaisesRegex(ValueError, 'seed differs'):
                open_store(root, replace(PACK, role='assistant'), 'control')
            open_store(root, PACK, 'control').close()

    def test_role_is_explicit_in_manifest_and_unknown_role_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'setting.md').write_text(ASSISTANT_SETTING)
            manifest = {'format':'her-author-pack/1','id':'assistant-control','as_of':'2026-10-30',
                        'role':'assistant','documents':[{'path':'setting.md','kind':'character'}]}
            (root/'pack.json').write_text(json.dumps(manifest))
            self.assertEqual(load_pack(root/'pack.json').role, 'assistant')
            manifest['role']='unknown'
            (root/'pack.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'Unknown conversation role'):
                load_pack(root/'pack.json')

    async def test_same_runtime_memory_and_tools_for_both_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            schemas, runtimes = [], []
            try:
                for n, pack in enumerate((PACK, assistant_control_pack(PACK))):
                    store = open_store(Path(directory)/str(n), pack, 'control', create=True)
                    lookup = WebLookup(FakeWeb(), CLOCK)
                    runtime = ConversationRuntime(pack, Scripted(), store=store,
                                                  writer=IdleWriter(), memory_tools=True, lookup=lookup)
                    runtimes.append(runtime)
                    turn = runtime.begin('我想聊建筑。')
                    self.assertEqual(store.pending()[0]['text'], '我想聊建筑。')
                    self.assertTrue(runtime.memory_tools)
                    schemas.append(lookup.tool_definitions())
                    self.assertEqual(turn.messages[-1], {'role':'user','content':'我想聊建筑。'})
                self.assertEqual(schemas[0], schemas[1])
                runtimes[0].store.append('isolation', 'user', 'ONLY_LEFT')
                self.assertNotIn('ONLY_LEFT', json.dumps(runtimes[1].store.pending()))
            finally:
                for runtime in runtimes:
                    await runtime.close(flush=False)
