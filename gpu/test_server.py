"""ASGI cancellation regression checks; fake model, no weights or GPU calls."""
import asyncio
import json
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from gpu import server


class OptimizationReadinessTests(unittest.TestCase):
    def test_config_and_health_do_not_call_unready_compilation_ready(self):
        def compiled():pass
        compiled._torchdynamo_orig_callable=lambda:None
        inner=SimpleNamespace(base_lm=SimpleNamespace(forward_step=compiled),
            residual_lm=SimpleNamespace(forward_step=compiled),feat_encoder=SimpleNamespace(_orig_mod=True),
            feat_decoder=SimpleNamespace(estimator=SimpleNamespace(_orig_mod=True)))
        candidate=SimpleNamespace(tts_model=inner)
        with patch.dict(os.environ,VOXCPM_COMPILE='1'),patch.object(server,'model',None):
            self.assertTrue(server.configured_optimization())
            self.assertFalse(server.health()['ready']);self.assertFalse(server.health()['optimization_active'])
        with patch.object(server,'model',candidate):self.assertTrue(server.health()['optimization_active'])
        inner.feat_decoder.estimator=object()
        self.assertFalse(server.optimized_model(candidate))
        with patch.dict(os.environ,VOXCPM_COMPILE='auto'):
            with self.assertRaisesRegex(ValueError,'VOXCPM_COMPILE'):server.configured_optimization()


class ControlLeaseTests(unittest.TestCase):
    def test_only_exact_transient_owner_writes_metadata_and_default_is_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'private').mkdir();(root/'gpu').mkdir()
            path=root/'private/control-heartbeat.json'
            with patch.object(server,'ROOT',root/'gpu'), patch.dict(os.environ,{'ANGELENO_CONTROL_INSTANCE':''}):
                server.control_heartbeat('a'*32);self.assertFalse(path.exists())
            with patch.object(server,'ROOT',root/'gpu'), patch.dict(os.environ,{'ANGELENO_CONTROL_INSTANCE':'a'*32}):
                server.control_heartbeat(None);server.control_heartbeat('b'*32);self.assertFalse(path.exists())
                with patch.object(server.time,'monotonic',return_value=123):server.control_heartbeat('a'*32)
                self.assertEqual(json.loads(path.read_text()),{'instance_id':'a'*32,'monotonic':123})
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                target=root/'unrelated';target.write_text('preserved')
                path.with_suffix('.next').symlink_to(target)
                with self.assertRaises(OSError):server.control_heartbeat('a'*32)
                self.assertEqual(target.read_text(),'preserved')


class AudioMetricsTests(unittest.TestCase):
    def test_default_off_never_constructs_metrics(self):
        with patch.dict(os.environ, {'ANGELENO_AUDIO_METRICS': '0'}), \
                patch.object(server, 'AudioMetrics', side_effect=AssertionError('No metrics')):
            self.assertIsNone(server.audio_metrics('not-a-safe-id'))

    def test_sample_counts_rtf_exclude_queue_wait_and_never_log_content(self):
        rows = []
        times = iter([0.0, 0.1, 0.2, 0.7, 10.7])
        metric = server.AudioMetrics('a' * 32, clock=lambda: next(times),
                                     write=lambda text, **_: rows.append(json.loads(text)))
        metric.event('gpu_chunk', size=9600)
        metric.event('gpu_queued', queue_chunks=4, wait_seconds=.1)
        metric.event('gpu_chunk', size=9600)
        self.assertAlmostEqual(rows[-1]['generated_audio_seconds'], .2)
        self.assertAlmostEqual(rows[-1]['generation_wall_seconds'], .6)
        self.assertAlmostEqual(rows[-1]['generation_rtf'], 3)
        self.assertAlmostEqual(rows[-1]['gap_seconds'], .6)
        self.assertEqual(rows[1]['queue_chunks'], 4)
        self.assertFalse({'text', 'pcm', 'reference', 'path'} & rows[-1].keys())
        metric.event('gpu_eof')
        self.assertAlmostEqual(rows[-1]['generation_rtf'], 3, msg='Drain time must not inflate inference RTF')

    def test_untrusted_correlation_is_not_echoed_and_writer_failure_is_nonfatal(self):
        with patch.dict(os.environ, {'ANGELENO_AUDIO_METRICS': '1'}):
            metric = server.audio_metrics('/private/reference.wav')
        self.assertRegex(metric.request_id, r'^[a-f0-9]{32}$')
        metric.write = lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic'))
        metric.event('gpu_eof')

    def test_process_row_cap_is_shared_and_does_not_restart_for_new_requests(self):
        rows = []
        with patch.object(server, 'AUDIO_METRIC_LIMIT', 2), patch.object(server, '_audio_metric_rows', 0):
            clock = MockClock()
            a = server.AudioMetrics('a'*32, clock=clock, write=lambda row, **_: rows.append(json.loads(row)))
            b = server.AudioMetrics('b'*32, clock=clock, write=lambda row, **_: rows.append(json.loads(row)))
            clock.now = 1; a.event('gpu_chunk', size=960)
            clock.now = 130; b.event('gpu_chunk', size=960)
            self.assertEqual(len(rows), 2)
            # Fixed row cap, not a per-request timer. Expiration cannot reset it.
            a.clock = lambda: (_ for _ in ()).throw(AssertionError('Capped sampler must be idle'))
            a.event('gpu_eof'); b.event('gpu_eof')
            c = server.AudioMetrics('c'*32, clock=clock, write=lambda row, **_: rows.append(json.loads(row)))
            c.event('gpu_chunk', size=960)
            self.assertEqual(len(rows), 2)


class MockClock:
    now = 0.0
    def __call__(self): return self.now


class FakeModel:
    def generate_streaming(self, **kwargs):
        for _ in range(100):
            yield np.zeros(480, dtype=np.float32)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reference_mode = patch.dict(os.environ, {'VOXCPM_REFERENCE_PURPOSE':'diagnostic', 'VOXCPM_REFERENCE_CACHE':'0'})
        self.reference_mode.start()

    async def asyncTearDown(self):
        self.reference_mode.stop()

    async def test_exhausted_metrics_do_not_stop_or_change_synthetic_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp)/'synthetic.wav'; reference.touch()
            with patch.dict(os.environ, {'VOXCPM_REFERENCE':str(reference), 'ANGELENO_AUDIO_METRICS':'1'}), \
                    patch.object(server, 'AUDIO_METRIC_LIMIT', 0), \
                    patch.object(server, 'model', FakeModel()), \
                    patch.object(server, 'generation_lock', threading.Lock()):
                response = await server.speech(server.Speech(input='公开合成输入。'))
                output = []
                async def receive(): await asyncio.Event().wait()
                async def send(message): output.append(message)
                await asyncio.wait_for(response({'type':'http','asgi':{'spec_version':'2.4'}}, receive, send), 5)
                self.assertEqual(output[0]['status'], 200)
                self.assertEqual(sum(len(m.get('body', b'')) for m in output), 96000)
                self.assertFalse(server.generation_lock.locked())

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
            with patch.dict(os.environ, {'VOXCPM_REFERENCE': str(reference),
                                         'VOXCPM_REFERENCE_PURPOSE':'diagnostic'}, clear=True):
                with self.assertRaisesRegex(ValueError, 'VOXCPM_MODEL'):
                    async with server.lifespan(server.app):
                        self.fail('No implicit model download')


class ReferenceContractTests(unittest.TestCase):
    def test_jo_is_operator_selection_and_digest_not_filename_or_voice_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp)/'legacy-name.bin';file.write_bytes(b'fixed synthetic reference')
            digest=hashlib.sha256(file.read_bytes()).hexdigest()
            env={'VOXCPM_REFERENCE_PURPOSE':'jo','VOXCPM_REFERENCE_ID':'explicit-selection',
                 'VOXCPM_REFERENCE_SHA256':digest}
            with patch.dict(os.environ,env,clear=True):
                value=server.validate_reference_asset(file)
                self.assertEqual(value,{'purpose':'jo','asset_id':'explicit-selection','sha256':digest,'verified':True})
                file.write_bytes(b'different synthetic reference')
                with self.assertRaisesRegex(ValueError,'digest'):
                    server.validate_reference_asset(file)
            # A file named Jo is not a selected Jo asset without the declaration.
            named=Path(tmp)/'jo-reference-no-bgm.wav';named.touch()
            with patch.dict(os.environ,{},clear=True):
                with self.assertRaisesRegex(ValueError,'PURPOSE'):server.validate_reference_asset(named)

    def test_missing_hash_diagnostic_and_unreadable_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'synthetic.wav';file.touch()
            with patch.dict(os.environ,{'VOXCPM_REFERENCE_PURPOSE':'jo'},clear=True):
                with self.assertRaisesRegex(ValueError,'REFERENCE_ID'):server.validate_reference_asset(file)
            with patch.dict(os.environ,{'VOXCPM_REFERENCE_PURPOSE':'diagnostic', 'VOXCPM_REFERENCE_CACHE':'0'},clear=True):
                value=server.validate_reference_asset(file)
                self.assertEqual(value['purpose'],'diagnostic');self.assertFalse(value['verified'])
                with patch.object(Path,'open',side_effect=PermissionError):
                    with self.assertRaisesRegex(ValueError,'not readable'):server.validate_reference_asset(file)




class ReferenceCacheTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, reference):
        calls = []
        closed = []
        class Tensor:
            def squeeze(self, _): return self
            def cpu(self): return self
            def numpy(self): return np.array([.25, -.25], dtype=np.float32)
        class TTS:
            sample_rate = 48000
            def build_prompt_cache(self, **kwargs):
                calls.append(('build', kwargs))
                return {'mode': 'reference', 'ref_audio_feat': object()}
            def generate_with_prompt_cache_streaming(self, **kwargs):
                calls.append(('generate', kwargs))
                try:
                    yield Tensor(), None, None
                    yield Tensor(), None, None
                finally:
                    closed.append(True)
        candidate = SimpleNamespace(tts_model=TTS())
        return candidate, calls, closed

    async def test_reference_encoded_once_with_fresh_generation_and_original_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'reference.wav';path.write_bytes(b'synthetic reference')
            candidate,calls,closed = self.fixture(path)
            with patch.dict(os.environ, VOXCPM_REFERENCE_CACHE='1'), patch.object(server, 'version', return_value='2.0.3'):
                voice = server.prepare_reference(candidate,path,Path(tmp))
            first = list(server.generate_stream(voice, 'Hello\n  world', path))
            second = list(server.generate_stream(voice, 'Another sentence.', path))
            self.assertEqual([x[0] for x in calls], ['build','generate','generate'])
            self.assertEqual(len(closed),2)
            self.assertEqual(first[0].tobytes(),second[0].tobytes())
            params = calls[1][1]
            self.assertEqual(params['target_text'],'Hello world')
            self.assertEqual((params['min_len'],params['max_len'],params['inference_timesteps'],params['cfg_value'],params['retry_badcase']), (2,4096,10,2.0,False))
            self.assertEqual(voice.key[0][0], '2.0.3')
            self.assertEqual(voice.key[1],hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(TypeError): voice.cache['mode']='continuation'
            self.assertEqual(set(voice.cache),{'mode','ref_audio_feat'})

    async def test_changed_asset_fails_instead_of_reusing_stale_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'reference.wav';path.write_bytes(b'first')
            candidate,calls,_=self.fixture(path)
            voice=server.ReferenceVoice(candidate,path,('2.0.3','model-instance'))
            path.write_bytes(b'other')
            with self.assertRaisesRegex(ValueError,'Reference asset changed'):
                list(server.generate_stream(voice,'Public text',path))
            self.assertEqual(len(calls),1)
            other=Path(tmp)/'other.wav';other.write_bytes(b'first')
            with self.assertRaises(ValueError):list(server.generate_stream(voice,'Public text',other))

    async def test_early_close_releases_generation_and_cache_survives_next_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'reference.wav';path.write_bytes(b'fixture')
            candidate,calls,closed=self.fixture(path)
            voice=server.ReferenceVoice(candidate,path,('test-model',))
            stream=server.generate_stream(voice,'First',path);next(stream);stream.close()
            self.assertEqual(closed,[True])
            self.assertEqual(len(list(server.generate_stream(voice,'Next',path))),2)
            self.assertEqual(len([x for x in calls if x[0]=='build']),1)

    async def test_cache_preparation_failure_never_publishes_ready(self):
        candidate=SimpleNamespace(tts_model=SimpleNamespace(sample_rate=48000,build_prompt_cache=lambda **_:(_ for _ in ()).throw(RuntimeError('synthetic cache failure'))))
        module=SimpleNamespace(VoxCPM=SimpleNamespace(from_pretrained=lambda *a,**k:candidate))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'reference.wav';path.write_bytes(b'fixture')
            with patch.dict(os.environ, VOXCPM_REFERENCE_CACHE='1',VOXCPM_REFERENCE=str(path),VOXCPM_MODEL=tmp,VOXCPM_REFERENCE_PURPOSE='diagnostic'), patch.dict('sys.modules',voxcpm=module), patch.object(server,'version',return_value='2.0.3'):
                with self.assertRaisesRegex(RuntimeError,'cache failure'):
                    async with server.lifespan(server.app):pass
            self.assertFalse(server.health()['ready'])


if __name__ == '__main__':
    unittest.main()
