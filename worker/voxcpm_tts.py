# SPDX-License-Identifier: Apache-2.0
# Modified from talk-to-fengge (Apache-2.0). See NOTICE and LICENSES.
"""VoxCPM2 流式 TTS plugin for LiveKit agents.

streaming=True：LLM 边生成边按句子边界触发 TTS，首句延迟目标 ~1.5s。
关键：aiohttp 使用 force_close=True 禁止连接复用，防止 SSH 隧道空闲超时
导致下一句 ServerDisconnectedError（这是6/17卡顿的根因）。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass

import aiohttp
from worker.pcm import PCMResampler
from her_agent.diagnostics import emit
from livekit.agents import APIConnectOptions, APIStatusError, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.tts import SynthesizeStream, TTS, TTSCapabilities

_SENT_ENDS = frozenset('。！？；!?;')

TARGET_SAMPLE_RATE = 24000


def _split_with_remainder(text: str) -> tuple[list[str], str]:
    """Return (complete_sentences, remainder). Splits on Chinese/English sentence ends."""
    parts = []
    last = 0
    for j, ch in enumerate(text):
        if ch in _SENT_ENDS:
            s = text[last:j + 1].strip()
            if s:
                parts.append(s)
            last = j + 1
    return parts, text[last:]


def _resample_pcm(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    import audioop
    if src_rate == dst_rate:
        return pcm
    result, _ = audioop.ratecv(pcm, 2, 1, src_rate, dst_rate, None)
    return result


@dataclass
class VoxCPMOptions:
    url: str
    voice: str
    style: str
    sample_rate: int


class VoxCPMHttpTTS(TTS):
    def __init__(
        self,
        *,
        url: str = "http://localhost:8000",
        voice: str = "jo",
        style: str = "",
        sample_rate: int = TARGET_SAMPLE_RATE,
        paid_enabled: bool = False,
    ) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        from worker.audio_budget import require_remote_permission
        require_remote_permission(url, paid_enabled=paid_enabled)
        self._opts = VoxCPMOptions(url=url, voice=voice, style=style, sample_rate=sample_rate)
        self._http_session: aiohttp.ClientSession | None = None
        self._server_sample_rate: int | None = None

    @property
    def provider(self) -> str:
        return "VoxCPM2-HTTP"

    @property
    def model(self) -> str:
        return "VoxCPM2"

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SynthesizeStream:
        return _VoxCPMSynthesizeStream(tts=self, conn_options=conn_options)

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS):
        return self._synthesize_with_stream(text, conn_options=conn_options)

    async def aclose(self) -> None:
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            # force_close=True：每次请求用新 TCP 连接，不复用。
            # 必须设置，否则 SSH 隧道在句子间隙空闲超时后断连，
            # aiohttp 复用旧连接会触发 ServerDisconnectedError。
            connector = aiohttp.TCPConnector(force_close=True)
            self._http_session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=120, sock_read=60),
            )
        return self._http_session

    async def _get_server_sample_rate(self) -> int:
        return 48000


class _VoxCPMSynthesizeStream(SynthesizeStream):
    """LLM token 流 → 句子边界切割 → VoxCPM 流式生成，句子级 pipelining。"""

    def __init__(self, *, tts: VoxCPMHttpTTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._vox = tts

    async def _run(self, output_emitter) -> None:
        request_id = str(uuid.uuid4())
        initialized = False
        server_sr = await self._vox._get_server_sample_rate()
        buffer = ""
        t0 = time.time()
        # Accumulate a small lead once per reply, not once per sentence.
        # This covers bursty transport without adding pauses at each boundary.
        lead_bytes = int(self._vox.sample_rate * 2 * max(0, min(1000,
            int(os.getenv('VOXCPM_PREBUFFER_MS', '100')))) / 1000)
        pending_pcm = bytearray()
        lead_ready = lead_bytes == 0
        measuring = os.getenv('ANGELENO_AUDIO_METRICS') == '1'
        pushed_samples = 0
        last_push = None
        metric_tag = None

        def push(data):
            nonlocal pushed_samples, last_push
            if not measuring:
                output_emitter.push(data)
                return
            before = time.monotonic()
            output_emitter.push(data)
            after = time.monotonic()
            pushed_samples += len(data) // 2
            # AudioEmitter has no public playout-queue duration API.
            # Do not relabel synchronous push time as actual queue depth.
            emit('audio_push', request_id=metric_tag, stream_id=request_id.replace('-', ''),
                 chunk_bytes=len(data), audio_seconds=pushed_samples / self._vox.sample_rate,
                 gap_seconds=before - last_push if last_push is not None else 0.0,
                 push_seconds=after - before, lead_queue_seconds=0.0,
                 sdk_queue_seconds=None)
            last_push = after

        def push_pcm(pcm):
            nonlocal lead_ready
            if lead_ready:
                push(pcm)
                return True
            pending_pcm.extend(pcm)
            if len(pending_pcm) >= lead_bytes:
                push(bytes(pending_pcm))
                pending_pcm.clear()
                lead_ready = True
                return True
            return False

        async def send_sentence(text: str) -> None:
            nonlocal initialized, metric_tag
            text = text.strip()
            if not text:
                return
            styled = f"({self._vox._opts.style}){text}" if self._vox._opts.style else text
            payload = {"input": styled, "voice": self._vox._opts.voice}
            if measuring:
                metric_tag = uuid.uuid4().hex  # one opaque correlation per sentence

            emitted = False
            last_exc: Exception | None = None
            for attempt in range(3):
                pending_before = len(pending_pcm)
                reason = "error"
                emit("tts_http_start", request_id=request_id, attempt=attempt + 1)
                try:
                    # 每次请求重新获取 session（force_close 保证新 TCP 连接）
                    sess = await self._vox._ensure_session()
                    headers = {'X-Angeleno-Audio-Request': metric_tag} if measuring else {}
                    received = 0
                    last_receive = None
                    receive_started = time.monotonic() if measuring else None
                    async with sess.post(
                        f"{self._vox._opts.url}/v1/audio/speech/stream",
                        json=payload,
                        **({'headers': headers} if measuring else {}),
                    ) as resp:
                        if resp.status >= 400:
                            body = await resp.text()
                            raise APIStatusError(
                                f"VoxCPM {resp.status}: {body[:200]}",
                                status_code=resp.status,
                                request_id=request_id,
                                body=body,
                            )

                        if not initialized:
                            output_emitter.initialize(
                                request_id=request_id,
                                sample_rate=self._vox.sample_rate,
                                num_channels=1,
                                mime_type="audio/pcm",
                            )
                            initialized = True

                        resampler = PCMResampler(server_sr, self._vox.sample_rate)
                        async for chunk in resp.content.iter_chunked(4096):
                            if not chunk:
                                continue
                            pcm = resampler.push(chunk)
                            if measuring:
                                now = time.monotonic()
                                received += len(chunk)
                                duration = (received // 2) / server_sr
                                emit('audio_http_chunk', request_id=metric_tag, stream_id=request_id.replace('-', ''),
                                     attempt=attempt + 1, chunk_bytes=len(chunk),
                                     elapsed_seconds=now - receive_started,
                                     gap_seconds=now - last_receive if last_receive is not None else now - receive_started,
                                     audio_seconds=duration,
                                     receive_rtf=(now - receive_started) / duration if duration else None,
                                     lead_queue_seconds=len(pending_pcm) / (self._vox.sample_rate * 2))
                                last_receive = now
                            if pcm:
                                emitted = push_pcm(pcm) or emitted
                        resampler.finish()
                        emit("tts_http_eof", request_id=request_id, attempt=attempt + 1)
                    reason = "eof"
                    return  # 成功，退出重试循环
                except asyncio.CancelledError:
                    reason = "cancelled"
                    raise
                except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, asyncio.TimeoutError) as exc:
                    reason = "transport_error"
                    if emitted:
                        raise  # Never replay a sentence after partial playback.
                    del pending_pcm[pending_before:]
                    last_exc = exc
                    print(f"[tts] voxcpm disconnect attempt={attempt+1}: {exc!r}", flush=True)
                    await asyncio.sleep(0.3)
                finally:
                    emit("tts_http_end", request_id=request_id, attempt=attempt + 1, reason=reason)
            raise RuntimeError(f"VoxCPM TTS failed after 3 attempts: {last_exc}")

        async for token_or_sentinel in self._input_ch:
            if isinstance(token_or_sentinel, SynthesizeStream._FlushSentinel):
                # LLM 结束或 flush() 调用 — 发送剩余 buffer
                if buffer.strip():
                    await send_sentence(buffer)
                    buffer = ""
            else:
                buffer += token_or_sentinel
                sentences, buffer = _split_with_remainder(buffer)
                for sentence in sentences:
                    await send_sentence(sentence)

        # input_ch 关闭后处理残留
        if buffer.strip():
            await send_sentence(buffer)

        # A reply shorter than the lead must still be spoken in full.
        if pending_pcm:
            push(bytes(pending_pcm))
            pending_pcm.clear()

        if not initialized:
            output_emitter.initialize(
                request_id=request_id,
                sample_rate=self._vox.sample_rate,
                num_channels=1,
                mime_type="audio/pcm",
            )
