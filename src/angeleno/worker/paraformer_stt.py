"""Paraformer realtime adapter; official DashScope duplex WebSocket protocol."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
import uuid

import aiohttp
from livekit import rtc
from angeleno.language import configured_language
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, DEFAULT_API_CONNECT_OPTIONS, stt


class ParaformerSTT(stt.STT):
    def __init__(self, api_key=None, url=None, *, allowance=None, paid_enabled=False, language=None):
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True))
        self.key = api_key or os.environ.get('DASHSCOPE_API_KEY', '')
        self.url = url or os.environ.get('DASHSCOPE_WS_URL', 'wss://dashscope.aliyuncs.com/api-ws/v1/inference')
        if not self.key:
            raise ValueError('DASHSCOPE_API_KEY is required')
        from angeleno.worker.audio_budget import require_remote_permission, is_loopback
        require_remote_permission(self.url, paid_enabled=paid_enabled)
        if not is_loopback(self.url) and allowance is None:
            raise ValueError('Remote ASR requires an explicit duration allowance')
        self.allowance = allowance
        self.language = configured_language() if language is None else configured_language({"ANGELENO_LANGUAGE": language})

    @property
    def model(self):
        return 'paraformer-realtime-v2'

    @property
    def provider(self):
        return 'dashscope'

    async def _recognize_impl(self, *args, **kwargs):
        raise NotImplementedError('Use realtime streaming')

    def stream(self, *, language=None, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        if self.allowance is not None:
            conn_options = replace(conn_options, max_retry=0)
        return ParaformerStream(self, conn_options, language=language or self.language)


class ParaformerStream(stt.SpeechStream):
    def __init__(self, owner, conn_options, *, language):
        super().__init__(stt=owner, conn_options=conn_options, sample_rate=16000)
        self.owner = owner
        self.language = configured_language({"ANGELENO_LANGUAGE": language})
        self.task_id = str(uuid.uuid4())
        self.speaking = False

    def message(self, action):
        msg = {'header': {'action': action, 'task_id': self.task_id, 'streaming': 'duplex'},
               'payload': {'input': {}}}
        if action == 'run-task':
            msg['payload'].update({
                'task_group': 'audio', 'task': 'asr', 'function': 'recognition',
                'model': self.owner.model, 'parameters': {
                    'format': 'pcm', 'sample_rate': 16000,
                    'language_hints': [self.language, 'zh' if self.language == 'en' else 'en'], 'max_sentence_silence': 500,
                    'semantic_punctuation_enabled': False,
                    'disfluency_removal_enabled': False, 'heartbeat': True,
                },
            })
        return msg

    def emit_result(self, sentence):
        text = sentence.get('text', '')
        if sentence.get('heartbeat') or not text:
            return
        if not self.speaking:
            self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
            self.speaking = True
        final = sentence.get('sentence_end', False)
        self._event_ch.send_nowait(stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT if final else stt.SpeechEventType.INTERIM_TRANSCRIPT,
            request_id=self.task_id, alternatives=[stt.SpeechData(
                # The service does not guarantee language identification; this is the configured primary language.
                language=self.language, text=text,
                start_time=(sentence.get('begin_time') or 0) / 1000,
                end_time=(sentence.get('end_time') or 0) / 1000,
            )],
        ))
        if final:
            self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
            self.speaking = False

    async def _run(self):
        try:
            await self._connect_and_run()
        except aiohttp.WSServerHandshakeError as exc:
            raise APIStatusError('Paraformer connection rejected', status_code=exc.status) from exc
        except asyncio.TimeoutError as exc:
            raise APITimeoutError('Paraformer connection timed out') from exc
        except aiohttp.ClientError as exc:
            raise APIConnectionError('Paraformer connection failed') from exc

    async def _connect_and_run(self):
        self.task_id = str(uuid.uuid4())
        self.speaking = False
        started = asyncio.Event()
        audio_credit = 0
        if self.owner.allowance is not None:
            self.owner.allowance.check_available()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(
            total=None, sock_connect=self._conn_options.timeout,
        )) as session:
            connection = await asyncio.wait_for(session.ws_connect(self.owner.url, headers={
                'Authorization': 'Bearer ' + self.owner.key,
            }, heartbeat=20), timeout=self._conn_options.timeout)
            async with connection as ws:
                await ws.send_json(self.message('run-task'))

                async def send():
                    nonlocal audio_credit
                    await asyncio.wait_for(started.wait(), self._conn_options.timeout)
                    async for item in self._input_ch:
                        if isinstance(item, rtc.AudioFrame):
                            pcm = bytes(item.data)
                            if self.owner.allowance is not None:
                                # Input is resampled by SpeechStream to 16kHz mono s16.
                                if item.sample_rate != 16000 or item.num_channels != 1:
                                    raise ValueError('Trial ASR requires 16kHz mono input')
                                if len(pcm) > audio_credit:
                                    seconds = (len(pcm) - audio_credit + 31999) // 32000
                                    self.owner.allowance.reserve(seconds)
                                    audio_credit += seconds * 32000
                                audio_credit -= len(pcm)
                            await ws.send_bytes(pcm)
                        # Per-turn flush is not a session close. Server VAD emits finals.
                    await ws.send_json(self.message('finish-task'))

                async def receive():
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = json.loads(msg.data)
                        event = data.get('header', {}).get('event')
                        if event == 'task-started':
                            started.set()
                        elif event == 'task-failed':
                            code = data['header'].get('error_code', 'unknown')
                            raise APIStatusError(f'Paraformer task failed: {code}', retryable=False)
                        elif event == 'task-finished':
                            return
                        elif event == 'result-generated':
                            self.emit_result(data.get('payload', {}).get('output', {}).get('sentence', {}))
                    raise APIStatusError('Paraformer WebSocket closed before task-finished')

                sender = asyncio.create_task(send())
                receiver = asyncio.create_task(receive())
                try:
                    done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    if sender in done:
                        await asyncio.wait_for(receiver, self._conn_options.timeout)
                finally:
                    sender.cancel()
                    receiver.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
