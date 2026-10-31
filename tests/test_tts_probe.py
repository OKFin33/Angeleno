"""Probe orchestration checks using in-memory transports, never sockets/GPU."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import probe_tts as probe


class Response:
    def __init__(self, chunks=(), *, status=200, data=None):
        self.status, self.data = status, data
        self.headers = {'Content-Type': 'audio/pcm', 'X-Sample-Rate': '48000'}
        self.chunks, self.content, self.closed = chunks, self, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.data

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            if isinstance(chunk, asyncio.Event):
                await chunk.wait()
            else:
                yield chunk

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses, health_busy=(False,)):
        self.responses, self.posts, self.gets = list(responses), [], []
        self.health_busy, self.health_index, self.closed = health_busy, 0, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def get(self, url, **kwargs):
        assert kwargs == {'allow_redirects': False}
        self.gets.append(url)
        busy = self.health_busy[min(self.health_index, len(self.health_busy) - 1)]
        self.health_index += 1
        return Response(data={'ready': True, 'sample_rate': 48000, 'busy': busy})

    def post(self, url, **kwargs):
        assert kwargs['allow_redirects'] is False
        self.posts.append(kwargs['json'])
        return self.responses.pop(0)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_and_incomplete_flags_never_construct_network_session(self):
        with patch('aiohttp.ClientSession', side_effect=AssertionError('network')), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(probe.main([]), 0)
            for argv in [ ['--run'], ['--url', 'http://127.0.0.1:18080'],
                          ['--run', '--url', 'http://127.0.0.1:18080'] ]:
                with self.assertRaises(SystemExit):
                    probe.main(argv)
        self.assertTrue(all(row['status'] == 'not_run' for row in probe.plan()['cases']))
        for url in ['https://example.com', 'http://user:pass@localhost', 'http://localhost/?key=x']:
            with self.assertRaises(ValueError):
                probe.endpoint(url)

    async def test_three_requests_record_separate_pcm_cancel_and_recovery(self):
        normal = Response([b'\x01\x00\x02', b'\x00'])
        cancel = Response([b'\x03\x00\x04\x00', b'ignored'])
        recovery = Response([b'\x05', b'\x00\x06\x00'])
        session = Session([normal, cancel, recovery], (False, False, True, False, False))
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'new'
            await probe.run_probe(report, 'http://127.0.0.1', out, cancel_bytes=4, session_factory=lambda: session)
            self.assertEqual((out / 'normal.pcm').read_bytes(), b'\x01\x00\x02\x00')
            self.assertEqual((out / 'cancel.pcm').read_bytes(), b'\x03\x00\x04\x00')
            self.assertEqual((out / 'recovery.pcm').read_bytes(), b'\x05\x00\x06\x00')
            recorded = json.loads((out / 'receipt.json').read_text())
        self.assertEqual(recorded['status'], 'transport_sequence_completed')
        self.assertEqual([r['status'] for r in recorded['cases']],
                         ['stream_completed', 'client_closed', 'stream_completed'])
        self.assertTrue(cancel.closed and session.closed)
        self.assertEqual(len(session.posts), 3)
        self.assertEqual(session.posts, [r['payload'] for r in probe.plan()['cases']])
        self.assertTrue(all(r['first_pcm_ms'] is not None for r in recorded['cases']))
        self.assertFalse(recorded['cases'][1]['eof'])
        self.assertIsNone(recorded['human_heard'])
        self.assertIsNone(recorded['server_cancel_acknowledged'])
        self.assertTrue(recorded['cases'][1]['post_cancel_busy_seen'])
        self.assertTrue(recorded['cases'][1]['post_cancel_idle_observed'])

    def test_nonfinite_or_nonpositive_timeouts_never_start_probe(self):
        with patch.object(probe, 'run_probe', side_effect=AssertionError('must not run')), \
                contextlib.redirect_stderr(io.StringIO()):
            for value in ['nan', 'inf', '-inf', '0', '-1']:
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    probe.main(['--run', '--url', 'http://127.0.0.1:18080',
                                '--output', 'tmp/unused', '--timeout=' + value])

    async def test_empty_truncated_and_wrong_format_audio_stop_sequence(self):
        for name, response in [('empty', Response([])), ('truncated', Response([b'\x01'])),
                               ('wrong_rate', Response([b'\x01\x00'])),
                               ('untrusted_headers', Response([b'\x01\x00']))]:
            if name == 'wrong_rate':
                response.headers['X-Sample-Rate'] = '24000'
            if name == 'untrusted_headers':
                response.headers = {'Content-Type': 'synthetic-secret-header',
                                    'X-Sample-Rate': 'synthetic-secret-rate'}
            report = probe.plan()
            session = Session([response])
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(probe.ProbeError):
                    await probe.run_probe(report, 'http://127.0.0.1', Path(tmp)/'new',
                                          session_factory=lambda: session)
                saved = (Path(tmp)/'new/receipt.json').read_text()
                self.assertNotIn('synthetic-secret', saved)
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['cases'][1]['status'], 'not_run')
            self.assertEqual(len(session.posts), 1)

    async def test_non48k_health_prevents_any_synthesis(self):
        session = Session([])
        session.get = lambda *args, **kwargs: Response(data={'ready': True, 'sample_rate': 24000, 'busy': False})
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(probe.ProbeError):
                await probe.run_probe(report, 'http://127.0.0.1', Path(tmp)/'new',
                                      session_factory=lambda: session)
        self.assertEqual(len(session.posts), 0)

    async def test_idle_after_cancel_does_not_claim_server_cancellation(self):
        session = Session([Response([b'\x01\x00']), Response([b'\x02\x00']), Response([b'\x03\x00'])])
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            await probe.run_probe(report, 'http://127.0.0.1', Path(tmp)/'new', cancel_bytes=2,
                                  session_factory=lambda: session)
        self.assertFalse(report['cases'][1]['post_cancel_busy_seen'])
        self.assertTrue(report['cases'][1]['post_cancel_idle_observed'])
        self.assertIsNone(report['server_cancel_acknowledged'])
        self.assertEqual(report['cases'][2]['status'], 'stream_completed')

    async def test_stalled_pcm_times_out_and_keeps_prefix(self):
        session = Session([Response([b'\x01\x00', asyncio.Event()])])
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'new'
            with self.assertRaises(TimeoutError):
                await probe.run_probe(report, 'http://127.0.0.1', out, timeout=0.01,
                                      session_factory=lambda: session)
            self.assertEqual((out/'normal.pcm').read_bytes(), b'\x01\x00')
        self.assertTrue(session.closed)
        self.assertEqual(report['cases'][1]['status'], 'not_run')

    async def test_unknown_exception_message_is_not_written(self):
        session = Session([Response([ValueError('synthetic-secret-exception')])])
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'new'
            with self.assertRaises(ValueError):
                await probe.run_probe(report, 'http://127.0.0.1', out, session_factory=lambda: session)
            self.assertNotIn('synthetic-secret', (out/'receipt.json').read_text())
        self.assertIsNone(report['error_reason'])

    async def test_short_cancel_stream_does_not_claim_cancel_or_send_recovery(self):
        session = Session([Response([b'\x01\x00']), Response([b'\x02\x00'])])
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'cancellation not exercised'):
                await probe.run_probe(report, 'http://127.0.0.1', Path(tmp)/'new', session_factory=lambda: session)
        self.assertFalse(report['cases'][1]['client_cancelled'])
        self.assertEqual(report['cases'][2]['status'], 'not_run')
        self.assertEqual(len(session.posts), 2)
        self.assertTrue(session.closed)

    async def test_partial_failure_is_preserved_and_never_retried(self):
        session = Session([Response([b'\x01\x00', ConnectionError('synthetic')])])
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'new'
            with self.assertRaises(ConnectionError):
                await probe.run_probe(report, 'http://127.0.0.1', out, session_factory=lambda: session)
            saved = json.loads((out/'receipt.json').read_text())
            self.assertEqual((out/'normal.pcm').read_bytes(), b'\x01\x00')
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['cases'][0]['pcm_bytes'], 2)
        self.assertEqual([r['status'] for r in saved['cases'][1:]], ['not_run', 'not_run'])
        self.assertEqual(len(session.posts), 1)

    async def test_http_error_records_stage_and_stops_without_retry(self):
        session = Session([Response(status=503)])
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'HTTP 503'):
                await probe.run_probe(report, 'http://127.0.0.1', Path(tmp)/'new',
                                      session_factory=lambda: session)
        self.assertEqual(report['cases'][0]['http_status'], 503)
        self.assertIn('not retried', report['error_reason'])
        self.assertEqual(report['cases'][1]['status'], 'not_run')
        self.assertEqual(len(session.posts), 1)

    async def test_cancel_lock_timeout_keeps_recovery_unrun(self):
        session = Session([Response([b'\x01\x00']), Response([b'\x02\x00'])], (False, False, True))
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TimeoutError):
                await probe.run_probe(report, 'http://127.0.0.1', Path(tmp)/'new', timeout=0.01,
                                      cancel_bytes=2, session_factory=lambda: session)
        self.assertTrue(report['cases'][1]['client_cancelled'])
        self.assertEqual(report['cases'][2]['status'], 'not_run')
        self.assertEqual(len(session.posts), 2)

    async def test_task_cancel_closes_session_and_saves_interrupted_prefix(self):
        session = Session([Response([b'\x01\x00', asyncio.Event()])])
        report = probe.plan()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'new'
            task = asyncio.create_task(probe.run_probe(report, 'http://127.0.0.1', out,
                                                       session_factory=lambda: session))
            for _ in range(100):
                if report['cases'][0].get('pcm_bytes') == 2:
                    break
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            saved = json.loads((out/'receipt.json').read_text())
        self.assertEqual(saved['status'], 'interrupted')
        self.assertTrue(session.closed)
        self.assertEqual(saved['cases'][1]['status'], 'not_run')

    async def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                await probe.run_probe(probe.plan(), 'http://127.0.0.1', Path(tmp),
                                      session_factory=lambda: self.fail('must not create session'))


if __name__ == '__main__':
    unittest.main()
