"""ASGI cancellation regression checks; fake model, no weights or GPU calls."""
import asyncio
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from gpu import server


class FakeModel:
    def generate_streaming(self, **kwargs):
        for _ in range(100):
            yield np.zeros(480, dtype=np.float32)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_waits_for_complete_warmup_and_clears_on_shutdown(self):
        started, finish = threading.Event(), threading.Event()
        calls = []

        class ColdModel:
            tts_model = SimpleNamespace(sample_rate=48000)

            def generate_streaming(self, **kwargs):
                calls.append(kwargs)
                started.set()
                finish.wait(2)
                yield np.zeros(480, dtype=np.float32)

        candidate = ColdModel()
        module = SimpleNamespace(VoxCPM=SimpleNamespace(from_pretrained=lambda *a, **k: candidate))
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / 'reference.wav'
            reference.touch()
            with patch.dict(os.environ, VOXCPM_REFERENCE=str(reference), VOXCPM_MODEL=tmp), \
                 patch.dict('sys.modules', voxcpm=module), \
                 patch.object(server, 'generation_lock', threading.Lock()):
                life = server.lifespan(server.app)
                entering = asyncio.create_task(life.__aenter__())
                try:
                    for _ in range(100):
                        if started.is_set():
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(started.is_set())
                    self.assertFalse(server.health()['ready'])
                    self.assertTrue(server.health()['busy'])
                    with self.assertRaises(server.HTTPException) as exc:
                        await server.speech(server.Speech(input='用户请求。'))
                    self.assertEqual(exc.exception.status_code, 503)
                    finish.set()
                    await asyncio.wait_for(entering, 2)
                    self.assertTrue(server.health()['ready'])
                    self.assertFalse(server.health()['busy'])
                    self.assertEqual(calls[0]['reference_wav_path'], str(reference))
                    self.assertFalse(calls[0]['retry_badcase'])
                    # The user path has exactly the same inference parameters.
                    list(server.generate_stream(candidate, '用户请求。', reference))
                    self.assertEqual({k: v for k, v in calls[0].items() if k != 'text'},
                                     {k: v for k, v in calls[1].items() if k != 'text'})
                    await life.__aexit__(None, None, None)
                    self.assertFalse(server.health()['ready'])
                finally:
                    finish.set()
                    if not entering.done():
                        entering.cancel()
                        await asyncio.gather(entering, return_exceptions=True)

    async def test_empty_invalid_and_failed_warmup_never_publish_ready(self):
        for kind in ('empty', 'invalid', 'exception', 'wrong_rate'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                reference = Path(tmp) / 'reference.wav'
                reference.touch()

                class BrokenModel:
                    tts_model = SimpleNamespace(sample_rate=44100 if kind == 'wrong_rate' else 48000)

                    def generate_streaming(self, **kwargs):
                        if kind == 'exception':
                            raise RuntimeError('private path must not escape')
                        if kind == 'invalid':
                            yield np.array([np.nan])

                module = SimpleNamespace(VoxCPM=SimpleNamespace(from_pretrained=lambda *a, **k: BrokenModel()))
                with patch.dict(os.environ, VOXCPM_REFERENCE=str(reference), VOXCPM_MODEL=tmp), \
                     patch.dict('sys.modules', voxcpm=module), \
                     patch.object(server, 'generation_lock', threading.Lock()):
                    with self.assertRaises(RuntimeError) as exc:
                        async with server.lifespan(server.app):
                            self.fail('Must not become ready')
                    self.assertNotIn('private path', str(exc.exception))
                    self.assertFalse(server.health()['ready'])
                    self.assertFalse(server.health()['busy'])

    async def test_warmup_timeout_fails_startup_without_releasing_active_gpu(self):
        finish = threading.Event()
        closed = threading.Event()

        class StuckModel:
            tts_model = SimpleNamespace(sample_rate=48000)

            def generate_streaming(self, **kwargs):
                try:
                    finish.wait(2)
                    yield np.zeros(480, dtype=np.float32)
                finally:
                    closed.set()

        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / 'reference.wav'
            reference.touch()
            module = SimpleNamespace(VoxCPM=SimpleNamespace(from_pretrained=lambda *a, **k: StuckModel()))
            with patch.dict(os.environ, VOXCPM_REFERENCE=str(reference), VOXCPM_MODEL=tmp), \
                 patch.dict('sys.modules', voxcpm=module), \
                 patch.object(server, 'generation_lock', threading.Lock()), \
                 patch.object(server, 'WARMUP_TIMEOUT_SECONDS', 0.03):
                try:
                    with self.assertRaises(TimeoutError):
                        async with server.lifespan(server.app):
                            self.fail('Timeout must fail startup')
                    self.assertFalse(server.health()['ready'])
                    self.assertTrue(server.health()['busy'])
                finally:
                    finish.set()
                    for _ in range(100):
                        if not server.generation_lock.locked():
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(closed.is_set())
                    self.assertFalse(server.generation_lock.locked())

    async def test_cancel_before_body_releases_gpu_and_next_request_works(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / 'reference.wav'
            reference.touch()
            with patch.dict(os.environ, VOXCPM_REFERENCE=str(reference)), \
                 patch.object(server, 'model', FakeModel()), \
                 patch.object(server, 'generation_lock', threading.Lock()):
                response = await server.speech(server.Speech(input='测试。'))
                self.assertFalse(server.generation_lock.locked())
                headers = asyncio.Event()

                async def stalled_send(message):
                    self.assertEqual(message['type'], 'http.response.start')
                    headers.set()
                    await asyncio.Event().wait()

                async def receive():
                    await asyncio.Event().wait()

                scope = {'type': 'http', 'asgi': {'spec_version': '2.4'}}
                task = asyncio.create_task(response(scope, receive, stalled_send))
                await asyncio.wait_for(headers.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                for _ in range(100):
                    if not server.generation_lock.locked():
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(server.generation_lock.locked())

                received = []

                async def send(message):
                    received.append(message)

                response = await server.speech(server.Speech(input='第二次。'))
                await asyncio.wait_for(response(scope, receive, send), 5)
                self.assertEqual(received[0]['status'], 200)
                self.assertGreater(sum(len(m.get('body', b'')) for m in received), 0)
                self.assertFalse(server.generation_lock.locked())

    async def test_missing_reference_or_local_model_refuses_before_gpu_import(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'VOXCPM_REFERENCE'):
                async with server.lifespan(server.app):
                    self.fail('Must refuse before loading a model')
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / 'synthetic.wav'
            reference.touch()
            with patch.dict(os.environ, {'VOXCPM_REFERENCE': str(reference)}, clear=True):
                with self.assertRaisesRegex(ValueError, 'VOXCPM_MODEL'):
                    async with server.lifespan(server.app):
                        self.fail('No implicit model download')


if __name__ == '__main__':
    unittest.main()
