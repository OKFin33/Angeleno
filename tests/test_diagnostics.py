"""Metadata only; fake TTS and no network or real private files."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from her_agent import diagnostics
from worker.voxcpm_tts import VoxCPMHttpTTS, _VoxCPMSynthesizeStream


class ReceiptTests(unittest.TestCase):
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
        with patch('worker.voxcpm_tts.emit') as emit:
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
