"""Metadata only; fake TTS and no network or real private files."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from angeleno import diagnostics
from angeleno.worker.voxcpm_tts import VoxCPMHttpTTS, _VoxCPMSynthesizeStream


class ReceiptTests(unittest.TestCase):
    def test_audio_rows_are_default_off_and_capped_across_streams_and_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(diagnostics, 'ROOT', Path(tmp)), \
                patch.object(diagnostics, 'AUDIO_METRIC_LIMIT', 2), patch.object(diagnostics, '_audio_metric_rows', 0):
            private = Path(tmp)/'private'; private.mkdir()
            a, b, later = (private/name for name in ('a.jsonl', 'b.jsonl', 'later.jsonl'))
            with patch.dict(os.environ, {'ANGELENO_DIAGNOSTICS_FILE':str(a), 'ANGELENO_AUDIO_METRICS':'0'}):
                self.assertFalse(diagnostics.emit('audio_http_chunk', chunk_bytes=960))
                self.assertFalse(a.exists()); self.assertEqual(diagnostics._audio_metric_rows, 0)
            with patch.dict(os.environ, {'ANGELENO_DIAGNOSTICS_FILE':str(a), 'ANGELENO_AUDIO_METRICS':'1'}), \
                    patch.object(diagnostics.time, 'monotonic', return_value=0):
                self.assertTrue(diagnostics.emit('audio_http_chunk', request_id='a', chunk_bytes=960))
            with patch.dict(os.environ, {'ANGELENO_DIAGNOSTICS_FILE':str(b), 'ANGELENO_AUDIO_METRICS':'1'}), \
                    patch.object(diagnostics.time, 'monotonic', return_value=130):
                self.assertTrue(diagnostics.emit('audio_push', request_id='b', chunk_bytes=960))
                self.assertFalse(diagnostics.emit('audio_push', request_id='c', chunk_bytes=960))
            with patch.dict(os.environ, {'ANGELENO_DIAGNOSTICS_FILE':str(later), 'ANGELENO_AUDIO_METRICS':'1'}), \
                    patch.object(diagnostics.os, 'open', side_effect=AssertionError('No file after cap')):
                self.assertFalse(diagnostics.emit('audio_http_chunk', request_id='new-stream', chunk_bytes=960))
            self.assertFalse(later.exists())
            self.assertEqual(sum(len(f.read_text().splitlines()) for f in (a,b)), 2)

    def test_explicit_private_file_only_and_no_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(diagnostics, 'ROOT', Path(tmp)):
            private = Path(tmp) / 'private'; private.mkdir()
            target = private / 'receipt.jsonl'
            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(diagnostics.emit('tts_http_eof', request_id='r1'))
                self.assertFalse(target.exists())
            with patch.dict(os.environ, {'ANGELENO_DIAGNOSTICS_FILE': str(target)}):
                self.assertFalse(diagnostics.emit('tts_http_eof', input='secret speech'))
                self.assertFalse(target.exists())
                self.assertTrue(diagnostics.emit('tts_http_eof', request_id='r1', attempt=1))
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                row = json.loads(target.read_text())
                self.assertEqual(set(row), {'event', 'monotonic_seconds', 'request_id', 'attempt'})
                target.chmod(0o644)
                self.assertFalse(diagnostics.emit('tts_http_eof'))
            for path in (Path(tmp) / 'public.jsonl', private / 'link.jsonl'):
                if path.name == 'link.jsonl':path.symlink_to(target)
                with patch.dict(os.environ, {'ANGELENO_DIAGNOSTICS_FILE': str(path)}):
                    self.assertFalse(diagnostics.emit('tts_http_eof'))
            self.assertFalse((Path(tmp) / 'public.jsonl').exists())


class TTSReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_capped_audio_observer_keeps_pcm_and_original_lifecycle_events(self):
        class Response:
            status = 200
            async def __aenter__(self): self.content = self; return self
            async def __aexit__(self, *_): pass
            async def iter_chunked(self, _): yield bytes(4800)
        tts = VoxCPMHttpTTS(url='http://127.0.0.1:18080')
        tts._ensure_session = AsyncMock(return_value=SimpleNamespace(post=Mock(return_value=Response())))
        async def tokens(): yield '公开合成输入。'
        with tempfile.TemporaryDirectory() as tmp, patch.object(diagnostics, 'ROOT', Path(tmp)), \
                patch.object(diagnostics, 'AUDIO_METRIC_LIMIT', 0):
            private=Path(tmp)/'private';private.mkdir();target=private/'metrics.jsonl'
            with patch.dict(os.environ, {'ANGELENO_AUDIO_METRICS':'1','ANGELENO_DIAGNOSTICS_FILE':str(target)}):
                output=Mock()
                await _VoxCPMSynthesizeStream._run(SimpleNamespace(_vox=tts,_input_ch=tokens()), output)
            self.assertEqual(sum(len(call.args[0]) for call in output.push.call_args_list), 2400)
            self.assertEqual([json.loads(x)['event'] for x in target.read_text().splitlines()],
                             ['tts_http_start','tts_http_eof','tts_http_end'])
        await tts.aclose()

    async def run_fake(self, cancel=False):
        class Response:
            status = 200
            async def __aenter__(self): self.content = self; return self
            async def __aexit__(self, *_): pass
            async def iter_chunked(self, _):
                if cancel:raise asyncio.CancelledError
                yield bytes(4800)
        tts = VoxCPMHttpTTS(url='http://127.0.0.1:18080')
        tts._ensure_session = AsyncMock(return_value=SimpleNamespace(post=Mock(return_value=Response())))
        async def tokens():yield 'public synthetic sentence.'
        stream = SimpleNamespace(_vox=tts, _input_ch=tokens())
        with patch('angeleno.worker.voxcpm_tts.emit') as emit:
            if cancel:
                with self.assertRaises(asyncio.CancelledError):
                    await _VoxCPMSynthesizeStream._run(stream, Mock())
            else:await _VoxCPMSynthesizeStream._run(stream, Mock())
        await tts.aclose()
        return emit.call_args_list

    async def test_http_eof_is_distinct_from_cancel_and_no_text_recorded(self):
        calls = await self.run_fake()
        self.assertEqual([c.args[0] for c in calls], ['tts_http_start', 'tts_http_eof', 'tts_http_end'])
        self.assertEqual(calls[-1].kwargs['reason'], 'eof')
        calls = await self.run_fake(cancel=True)
        self.assertEqual([c.args[0] for c in calls], ['tts_http_start', 'tts_http_end'])
        self.assertEqual(calls[-1].kwargs['reason'], 'cancelled')
        self.assertNotIn('synthetic sentence', str(calls))
