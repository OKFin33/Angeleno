"""Language configuration preserves character and source contracts; no real API."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from her_agent.author import assistant_control_pack, load_pack, reading_sections
from her_agent.baseline import author_instructions
from her_agent.language import configured_language
from her_agent.memory import seed_digest
from her_agent.runtime import ConversationRuntime, open_store
from worker.paraformer_stt import ParaformerSTT
from worker.voxcpm_tts import _split_with_remainder

ROOT = Path(__file__).resolve().parents[1]

class LanguageTests(unittest.TestCase):
    def test_default_and_explicit_values_fail_closed_on_unknown_language(self):
        self.assertEqual(configured_language({}), 'en')
        self.assertEqual(configured_language({'ANGELENO_LANGUAGE':'zh'}), 'zh')
        for value in ['', 'english', 'fr', None]:
            with self.assertRaises(ValueError):configured_language({'ANGELENO_LANGUAGE':value})

    def test_language_changes_no_author_bytes_source_ids_or_seed(self):
        pack=load_pack(ROOT/'characters/jo/author-pack.json')
        seed=seed_digest(pack);provenance=pack.provenance()
        zh=pack.instructions(today='2026-11-02',language='zh')
        en=pack.instructions(today='2026-11-02',language='en')
        self.assertIn('Default to natural spoken English',en)
        self.assertIn('用户明确纠正优先',en)
        self.assertNotIn('用适合口头交流的中文说话',en)
        self.assertIn('用适合口头交流的中文说话',zh)
        for doc in pack.documents:
            for section in reading_sections(doc.body):
                self.assertIn(section,en);self.assertIn(section,zh)
        self.assertEqual(seed,seed_digest(pack));self.assertEqual(provenance,pack.provenance())
        control=assistant_control_pack(pack).instructions(language='en')
        self.assertIn('你是用英语自然交谈',control)
        self.assertTrue(control.startswith('你是用英语自然交谈'))
        with patch.dict('os.environ',{'ANGELENO_LANGUAGE':'zh'}):
            self.assertIn('Default to natural spoken English',author_instructions({'ANGELENO_LANGUAGE':'en'}))

    def test_periods_protect_decimals_time_abbreviations_and_names_across_chunks(self):
        text='USC is in Los Angeles. Admission is $12.50 at 7:30 p.m. Bring ID! Next.'
        for split in range(len(text)+1):
            out,rem=_split_with_remainder(text[:split])
            more,rem=_split_with_remainder(rem+text[split:]);out+=more
            self.assertEqual(''.join((''.join(out)+rem).split()),''.join(text.split()))
            self.assertFalse(any(p.endswith(('$12.','7:30 p.','Los')) for p in out))
        self.assertEqual(_split_with_remainder('Hello. Next sentence.'),(['Hello.'],' Next sentence.'))
        self.assertEqual(_split_with_remainder('Dr. Chen likes L.A. architecture. Next'),(['Dr. Chen likes L.A. architecture.'],' Next'))
        self.assertEqual(_split_with_remainder('你好。Still here?'),(['你好。','Still here?'],''))
        self.assertEqual(_split_with_remainder('The price is $12.50'),([], 'The price is $12.50'))

class LanguagePreflightTests(unittest.TestCase):
    def test_development_memory_override_is_used_without_moving_account_ledgers(self):
        from test_voice_runner import configuration
        from scripts.run_voice import check_configuration
        from her_agent.baseline import configured_pack
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=configuration(root)
            env.update(ANGELENO_MEMORY_CREATE='0',ANGELENO_MEMORY_USER='dev-language',
                       ANGELENO_MEMORY_PURPOSE='development',ANGELENO_MEMORY_DATA_DIR='private/development-state')
            pack=configured_pack(env,root=root)
            open_store(root,pack,'dev-language',create=True,data_dir='private/development-state',purpose='development').close()
            self.assertEqual(check_configuration(env,root=root),[])
            self.assertFalse((root/'private/state').exists())
            self.assertIn('ANGELENO_LANGUAGE must be en or zh',check_configuration({**env,'ANGELENO_LANGUAGE':'fr'},root=root))

class ASRLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_language_and_stream_override_use_16k_pcm_and_correct_tags(self):
        from livekit.agents import stt
        owner=ParaformerSTT(api_key='synthetic',url='ws://127.0.0.1:1',language='en')
        # Construct streams without opening sockets; run-task and emit_result are pure adapter seams.
        with patch('worker.paraformer_stt.ParaformerStream._run', return_value=None):
            for language,hints in [('en',['en','zh']),('zh',['zh','en'])]:
                stream=owner.stream(language=language)
                params=stream.message('run-task')['payload']['parameters']
                self.assertEqual(params['language_hints'],hints)
                self.assertEqual((params['format'],params['sample_rate']),('pcm',16000))
                stream.emit_result({'text':'USC at 7:30 p.m.','sentence_end':True})
                events=[await stream._event_ch.recv() for _ in range(3)]
                final=next(e for e in events if e.type==stt.SpeechEventType.FINAL_TRANSCRIPT)
                self.assertEqual(final.alternatives[0].language,language)
                await stream.aclose()
        await owner.aclose()

class RuntimeLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_and_temporary_requests_keep_selected_language_and_seed(self):
        from test_lookup import IdleWriter, Scripted
        pack=load_pack(ROOT/'characters/jo/author-pack.json')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for language in ['en','zh']:
                store=open_store(root,pack,'dev-language',create=True,purpose='development')
                runtime=ConversationRuntime(pack,Scripted(),store=store,writer=IdleWriter(),language=language)
                turn=runtime.begin("Please don't save this conversation.")
                self.assertTrue(turn.temporary)
                self.assertEqual('Default to natural spoken English' in turn.messages[0]['content'],language=='en')
                self.assertEqual(store.records(),[])
                await runtime.close(flush=False)
