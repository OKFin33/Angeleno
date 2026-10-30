"""ASGI cancellation regression checks; fake model, no weights or GPU calls."""
import asyncio
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from gpu import server


class FakeModel:
    def generate_streaming(self, **kwargs):
        for _ in range(100):
            yield np.zeros(480, dtype=np.float32)


class ServerTests(unittest.IsolatedAsyncioTestCase):
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
