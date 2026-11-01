"""Local protocol checks. Fake audio here is not model-quality evidence."""
import asyncio
import audioop
import os
import struct
import unittest
from unittest.mock import patch

from aiohttp import web
from livekit import rtc
from livekit.agents import APIConnectOptions, APIConnectionError, APIStatusError, APITimeoutError, stt
from worker.pcm import PCMResampler
from worker.paraformer_stt import ParaformerSTT
from worker.voxcpm_tts import VoxCPMHttpTTS


class PCMTests(unittest.TestCase):
    def test_odd_network_chunks_preserve_samples(self):
        raw = struct.pack('<100h', *range(100))
        for rate in [48000, 24000]:
            converter = PCMResampler(48000, rate)
            result = b''.join(converter.push(raw[i:i+3]) for i in range(0, len(raw), 3))
            converter.finish()
            expected = raw if rate == 48000 else audioop.ratecv(raw, 2, 1, 48000, rate, None)[0]
            self.assertEqual(result, expected)

    def test_truncated_sample_is_an_error(self):
        converter = PCMResampler(48000, 48000)
        converter.push(b'\x00')
        with self.assertRaises(ValueError):
            converter.finish()


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runners = []

    async def asyncTearDown(self):
        for runner in self.runners:
            await runner.cleanup()

    async def serve(self, path, handler):
        app = web.Application()
        app.router.add_route('*', path, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        self.runners.append(runner)
        return f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'

    async def test_paraformer_stream_waits_for_start_and_keeps_turn_flush_open(self):
        actions = []
        audio = bytearray()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            start = await ws.receive_json()
            actions.append(start['header']['action'])
            self.assertEqual(start['payload']['parameters']['sample_rate'], 16000)
            await ws.send_json({'header': {'event': 'task-started'}})
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    audio.extend(msg.data)
                    await ws.send_json({'header': {'event': 'result-generated'}, 'payload': {'output': {'sentence': {
                        'text': '测试。', 'begin_time': 250, 'end_time': 750, 'sentence_end': True,
                    }}}})
                elif msg.type == web.WSMsgType.TEXT:
                    import json
                    actions.append(json.loads(msg.data)['header']['action'])
                    await ws.send_json({'header': {'event': 'task-finished'}})
                    break
            await ws.close()
            return ws

        url = (await self.serve('/asr', handler)).replace('http:', 'ws:')
        recognizer = ParaformerSTT(api_key='synthetic-test-key', url=url + '/asr')
        stream = recognizer.stream(conn_options=APIConnectOptions(max_retry=0, timeout=2))
        raw = b'\x00\x01' * 160
        stream.push_frame(rtc.AudioFrame(raw, 16000, 1, 160))
        stream.flush()
        stream.push_frame(rtc.AudioFrame(raw, 16000, 1, 160))
        stream.end_input()
        events = [ev async for ev in stream]
        await stream.aclose()
        await recognizer.aclose()
        finals = [e for e in events if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT]
        self.assertEqual(actions, ['run-task', 'finish-task'])
        self.assertEqual(audio, raw * 2)
        self.assertEqual(len(finals), 2)
        self.assertEqual(finals[0].alternatives[0].start_time, 0.25)

    async def test_asr_allowance_stops_audio_before_unreserved_second(self):
        from pathlib import Path
        import tempfile
        from worker.audio_budget import ASRAllowance
        audio = bytearray()

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.receive_json()
            await ws.send_json({'header': {'event': 'task-started'}})
            async for message in ws:
                if message.type == web.WSMsgType.BINARY:
                    audio.extend(message.data)
            return ws

        url = (await self.serve('/asr', handler)).replace('http:', 'ws:')
        with tempfile.TemporaryDirectory() as tmp:
            allowance = ASRAllowance(Path(tmp) / 'asr.sqlite3', 1)
            recognizer = ParaformerSTT(api_key='synthetic', url=url + '/asr', allowance=allowance)
            stream = recognizer.stream(conn_options=APIConnectOptions(max_retry=2, timeout=1))
            raw = bytes(32000)
            stream.push_frame(rtc.AudioFrame(raw, 16000, 1, 16000))
            stream.push_frame(rtc.AudioFrame(raw, 16000, 1, 16000))
            stream.end_input()
            try:
                with self.assertRaises(Exception):
                    _ = [event async for event in stream]
            finally:
                await stream.aclose()
                await recognizer.aclose()
            self.assertEqual(len(audio), 32000, 'Only the already reserved first second reaches the transport')

    async def test_tts_delivers_pcm_across_odd_chunks_and_sentences(self):
        texts = []
        raw = struct.pack('<2400h', *([500, -500] * 1200))

        async def handler(request):
            texts.append((await request.json())['input'])
            resp = web.StreamResponse(headers={'Content-Type': 'audio/pcm'})
            await resp.prepare(request)
            for i in range(0, len(raw), 301):
                await resp.write(raw[i:i+301])
                await asyncio.sleep(0)
            await resp.write_eof()
            return resp

        url = await self.serve('/v1/audio/speech/stream', handler)
        tts = VoxCPMHttpTTS(url=url, sample_rate=48000)
        stream = tts.stream(conn_options=APIConnectOptions(max_retry=0))
        stream.push_text('你好。第二句话。')
        stream.end_input()
        received = bytearray()
        async for item in stream:
            received.extend(bytes(item.frame.data))
        await stream.aclose()
        await tts.aclose()
        self.assertEqual(texts, ['你好。', '第二句话。'])
        self.assertEqual(received[:len(raw)*2], raw * 2)
        # The SDK may pad the final 10ms frame with zeros.
        self.assertTrue(all(b == 0 for b in received[len(raw)*2:]))

    async def test_adjacent_sentences_reuse_connection_and_owner_close_releases_it(self):
        connections=[];raw=b'\x01\x00'*9600
        async def handler(request):
            connections.append(request.transport)
            await request.json()
            return web.Response(body=raw,content_type='audio/pcm')
        tts=VoxCPMHttpTTS(url=await self.serve('/v1/audio/speech/stream',handler),sample_rate=48000)
        try:
            for text in ('第一句。','第二句。'):
                async with tts.synthesize(text,conn_options=APIConnectOptions(max_retry=0)) as stream:
                    received=b''.join([bytes(item.frame.data) async for item in stream])
                self.assertEqual(received[:len(raw)],raw)
            self.assertIs(connections[0],connections[1])
            self.assertFalse(tts._http_session.closed)
        finally:await tts.aclose()
        for _ in range(10):
            if connections[0].is_closing():break
            await asyncio.sleep(.01)
        self.assertTrue(connections[0].is_closing())

    async def test_idle_expiry_and_server_closed_socket_reconnect_without_replay(self):
        import worker.voxcpm_tts as module
        for expire in (False,True):
            connections=[];raw=b'\x01\x00'*9600
            async def handler(request):
                connections.append(request.transport);await request.json()
                return web.Response(body=raw,content_type='audio/pcm')
            with patch.object(module,'HTTP_KEEPALIVE_SECONDS',.02 if expire else 2):
                tts=VoxCPMHttpTTS(url=await self.serve('/v1/audio/speech/stream',handler),sample_rate=48000)
                try:
                    for index in range(2):
                        async with tts.synthesize('合成句。',conn_options=APIConnectOptions(max_retry=0)) as stream:
                            self.assertTrue([item async for item in stream])
                        if index==0:
                            if not expire:connections[0].close()
                            await asyncio.sleep(.04)
                    self.assertEqual(len(connections),2)
                    self.assertIsNot(connections[0],connections[1])
                finally:await tts.aclose()

    async def test_cancelled_body_is_closed_not_reused_and_next_sentence_succeeds(self):
        connections=[];release=asyncio.Event();raw=b'\x01\x00'*19200
        async def handler(request):
            connections.append(request.transport);await request.json()
            if len(connections)>1:return web.Response(body=raw,content_type='audio/pcm')
            response=web.StreamResponse(headers={'Content-Length':str(len(raw)*2)})
            await response.prepare(request);await response.write(raw)
            await release.wait()
            return response
        tts=VoxCPMHttpTTS(url=await self.serve('/v1/audio/speech/stream',handler),sample_rate=48000)
        stream=tts.synthesize('被打断。',conn_options=APIConnectOptions(max_retry=0))
        try:
            await asyncio.wait_for(stream.__anext__(),1)
            await asyncio.wait_for(stream.aclose(),.5)
            for _ in range(20):
                if connections[0].is_closing():break
                await asyncio.sleep(.01)
            self.assertTrue(connections[0].is_closing())
            async with tts.synthesize('下一句。',conn_options=APIConnectOptions(max_retry=0)) as next_stream:
                received=b''.join([bytes(item.frame.data) async for item in next_stream])
            self.assertEqual(received[:len(raw)],raw)
            self.assertEqual(len(connections),2,'cancelled audio must not be replayed')
            self.assertIsNot(connections[0],connections[1])
        finally:
            release.set();await stream.aclose();await tts.aclose()

    async def test_audio_metrics_opt_in_preserves_pcm_and_counts_short_sentence_flush(self):
        raw = struct.pack('<2400h', *([500, -500] * 1200))
        for flag in ('0', '1'):
            headers, notes = [], []
            async def handler(request):
                headers.append(request.headers.get('X-Angeleno-Audio-Request'))
                self.assertEqual(set(await request.json()), {'input', 'voice'})
                response = web.StreamResponse()
                await response.prepare(request)
                for i in range(0, len(raw), 301):
                    await response.write(raw[i:i+301])
                await response.write_eof()
                return response
            url = await self.serve('/v1/audio/speech/stream', handler)
            with patch.dict(os.environ, {'ANGELENO_AUDIO_METRICS': flag}), \
                    patch('worker.voxcpm_tts.emit', side_effect=lambda event, **kw: notes.append((event, kw))):
                tts = VoxCPMHttpTTS(url=url, sample_rate=48000)
                stream = tts.stream(conn_options=APIConnectOptions(max_retry=0))
                stream.push_text('合成一句。再一句。'); stream.end_input()
                received = b''.join([bytes(item.frame.data) async for item in stream])
                await stream.aclose(); await tts.aclose()
            self.assertEqual(received[:len(raw)*2], raw*2)
            metrics = [(event, value) for event, value in notes if event.startswith('audio_')]
            if flag == '0':
                self.assertEqual(headers, [None, None]); self.assertEqual(metrics, [])
            else:
                self.assertEqual(len(set(headers)), 2)
                for value in headers: self.assertRegex(value, r'^[a-f0-9]{32}$')
                chunks = [v for event, v in metrics if event == 'audio_http_chunk']
                self.assertEqual(sum(v['chunk_bytes'] for v in chunks), len(raw)*2)
                self.assertEqual({v['request_id'] for v in chunks}, set(headers))
                for value in chunks:
                    self.assertAlmostEqual(value['audio_seconds'] * 48000,
                                           round(value['audio_seconds'] * 48000))
                pushed = [v for event, v in metrics if event == 'audio_push']
                self.assertAlmostEqual(pushed[-1]['audio_seconds'], .1)
                self.assertTrue(all(v['sdk_queue_seconds'] is None for v in pushed))
                self.assertFalse(any({'text', 'input', 'voice', 'pcm'} & v.keys() for _, v in metrics))

    async def test_paraformer_retries_503_but_not_authentication_failure(self):
        for status, expected_calls in [(503, 3), (401, 1)]:
            calls = 0

            async def handler(request):
                nonlocal calls
                calls += 1
                return web.Response(status=status)

            url = (await self.serve('/asr', handler)).replace('http:', 'ws:')
            recognizer = ParaformerSTT(api_key='synthetic-test-key', url=url + '/asr')
            stream = recognizer.stream(conn_options=APIConnectOptions(max_retry=2, retry_interval=0.01, timeout=2))
            stream.end_input()
            try:
                with self.assertRaises((APIConnectionError, APIStatusError)):
                    async for _ in stream:
                        pass
            finally:
                await stream.aclose()
                await recognizer.aclose()
            self.assertEqual(calls, expected_calls)

    async def test_paraformer_times_out_when_connected_without_handshake_response(self):
        async def handler(request):
            await asyncio.sleep(0.4)
            return web.Response(status=503)

        url = (await self.serve('/asr', handler)).replace('http:', 'ws:')
        recognizer = ParaformerSTT(api_key='synthetic-test-key', url=url + '/asr')
        stream = recognizer.stream(conn_options=APIConnectOptions(max_retry=0, timeout=0.05))
        stream.end_input()

        async def collect():
            return [event async for event in stream]

        try:
            with self.assertRaises(APITimeoutError):
                await asyncio.wait_for(collect(), timeout=0.3)
        finally:
            await stream.aclose()
            await recognizer.aclose()

    async def test_tts_retries_before_audio_but_never_replays_partial_output(self):
        # More than the 100ms default lead, so partial=True has actually been emitted.
        raw = b'\x00\x01' * 19200
        for partial in [False, True]:
            calls = 0

            async def handler(request):
                nonlocal calls
                calls += 1
                if calls == 1:
                    resp = web.StreamResponse(headers={'Content-Type': 'audio/pcm', 'Content-Length': str(len(raw) * 2)})
                    await resp.prepare(request)
                    if partial:
                        await resp.write(raw)
                        await asyncio.sleep(0.05)
                    request.transport.close()
                    return resp
                return web.Response(body=raw, content_type='audio/pcm')

            url = await self.serve('/v1/audio/speech/stream', handler)
            tts = VoxCPMHttpTTS(url=url, sample_rate=48000)
            stream = tts.stream(conn_options=APIConnectOptions(max_retry=2, retry_interval=0.01))
            stream.push_text('测试。')
            stream.end_input()
            try:
                if partial:
                    with self.assertRaises(Exception):
                        async for _ in stream:
                            pass
                else:
                    received = [item async for item in stream]
                    self.assertTrue(received)
            finally:
                await stream.aclose()
                await tts.aclose()
            self.assertEqual(calls, 1 if partial else 2)

    async def test_tts_retries_unplayed_lead_without_duplicating_pcm(self):
        raw = struct.pack('<2400h', *([700, -700] * 1200))
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                resp = web.StreamResponse(headers={'Content-Length': str(len(raw) * 2)})
                await resp.prepare(request)
                await resp.write(raw)
                await asyncio.sleep(0.02)
                request.transport.close()
                return resp
            return web.Response(body=raw, content_type='audio/pcm')

        tts = VoxCPMHttpTTS(url=await self.serve('/v1/audio/speech/stream', handler), sample_rate=48000)
        stream = tts.stream(conn_options=APIConnectOptions(max_retry=0))
        stream.push_text('短回复。')
        stream.end_input()
        try:
            result = b''.join([bytes(x.frame.data) async for x in stream])
        finally:
            await stream.aclose()
            await tts.aclose()
        self.assertEqual(calls, 2)
        self.assertEqual(result[:len(raw)], raw)
        self.assertTrue(all(x == 0 for x in result[len(raw):]))



class AudioBudgetTests(unittest.TestCase):
    def test_remote_audio_requires_explicit_permission_and_asr_allowance(self):
        with self.assertRaisesRegex(ValueError, 'ANGELENO_PAID_ENABLED'):
            ParaformerSTT(api_key='synthetic', url='wss://example.invalid/asr')
        with self.assertRaisesRegex(ValueError, 'duration allowance'):
            ParaformerSTT(api_key='synthetic', url='wss://example.invalid/asr', paid_enabled=True)
        with self.assertRaisesRegex(ValueError, 'ANGELENO_PAID_ENABLED'):
            VoxCPMHttpTTS(url='https://example.invalid')

    def test_duration_reservations_survive_restart_and_reject_enlargement(self):
        from pathlib import Path
        import tempfile
        from worker.audio_budget import ASRAllowance, BudgetExceeded
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asr.sqlite3'
            allowance = ASRAllowance(path, 3)
            allowance.reserve(2)
            ASRAllowance(path, 3).reserve(1)
            with self.assertRaises(BudgetExceeded):
                allowance.reserve(1)
            with self.assertRaises(BudgetExceeded):
                ASRAllowance(path, 3).check_available()
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'differs'):
                ASRAllowance(path, 4)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_foreign_ledger_is_left_untouched(self):
        from pathlib import Path
        import sqlite3
        import tempfile
        from worker.audio_budget import ASRAllowance
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'other.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE other(value TEXT)')
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'left untouched'):
                ASRAllowance(path, 5)
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
