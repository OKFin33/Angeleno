"""Runtime storage is independent of shipped application assets."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from angeleno.paths import data_directory, private_file, resource_path
from angeleno.baseline import configured_pack
from angeleno.worker.audio_budget import asr_settings
from angeleno.voice import voice_configuration
from angeleno import diagnostics, trace


class ApplicationPathsTests(unittest.TestCase):
    def test_public_assets_do_not_depend_on_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            pack=configured_pack({},root=root)
            self.assertEqual(pack.id,'jo')
            self.assertEqual(len(pack.documents),4)
            self.assertTrue(resource_path('web/index.html').is_file())
            self.assertTrue(resource_path('characters/jo/NOTICE').is_file())
            self.assertFalse(list(root.iterdir()))
        with self.assertRaises(ValueError):resource_path('../outside')

    def test_external_trace_and_metadata_retain_file_and_purpose_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=root/'operator-state';state.mkdir()
            env={'ANGELENO_DATA_DIR':str(state),'ANGELENO_MEMORY_PURPOSE':'development',
                 'ANGELENO_TRACE':'1','ANGELENO_DIAGNOSTICS_FILE':str(state/'events.jsonl')}
            sink=trace.Trace(env,root=root)
            self.assertTrue(sink.write(trace.Binding(sink,'synthetic','t1'),'turn_input',{'text':'synthetic'}))
            self.assertEqual(len(trace.load_rows(sink.path,'development',root=root,env=env)),1)
            self.assertEqual(sink.path.stat().st_mode&0o777,0o600)
            with self.assertRaises(ValueError):trace.load_rows(sink.path,'real_user',root=root,env=env)
            with patch.dict(os.environ,env,clear=True),patch.object(diagnostics,'ROOT',root):
                self.assertTrue(diagnostics.emit('tts_http_start',request_id='synthetic'))
            link=state/'link.jsonl';link.symlink_to(state/'events.jsonl')
            with self.assertRaises(ValueError):private_file(link,env,root=root)
            with self.assertRaises(ValueError):private_file(root/'elsewhere.jsonl',env,root=root)

    def test_external_asr_ledger_stays_within_selected_data_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=root/'operator-state'
            env={'ANGELENO_DATA_DIR':str(state),'ANGELENO_ASR_MAX_SECONDS':'7'}
            path,seconds=asr_settings(env,root=root)
            self.assertEqual(path,(state/'asr.sqlite3').resolve());self.assertEqual(seconds,7)
            with self.assertRaises(ValueError):asr_settings({**env,'ANGELENO_ASR_LEDGER':str(root/'elsewhere.sqlite3')},root=root)
            self.assertFalse(state.exists())

    def test_development_voice_keeps_separate_storage_outside_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            env={'ANGELENO_MEMORY_PURPOSE':'development','ANGELENO_MEMORY_USER':'dev-paths',
                 'ANGELENO_DATA_DIR':str(root/'real'),'ANGELENO_MEMORY_DATA_DIR':str(root/'development')}
            self.assertEqual(voice_configuration(env,root=root)[1],'dev-paths')
            with self.assertRaises(ValueError):voice_configuration({**env,'ANGELENO_MEMORY_DATA_DIR':str(root/'real')},root=root)
            self.assertFalse(list(root.iterdir()))

    def test_empty_data_directory_is_not_the_source_or_install_directory(self):
        with self.assertRaises(ValueError):data_directory({'ANGELENO_DATA_DIR':''})
        with self.assertRaises(ValueError):asr_settings({'ANGELENO_DATA_DIR':' ', 'ANGELENO_ASR_MAX_SECONDS':'7'})
