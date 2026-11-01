"""Angeleno PCM endpoint, backed by official VoxCPM2 on CUDA.

One local GPU, one active synthesis. No denoiser, no cloud reference upload.
Bind to loopback and reach it through SSH. Raw audio is 48 kHz mono s16le.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
import json
import re
import time
import uuid
import hashlib
from pathlib import Path
import queue
import threading

import numpy as np
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('HF_HOME', str(ROOT.parent / 'private/cache'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
model = None
reference_state = None
generation_lock = threading.Lock()
WARMUP_TIMEOUT_SECONDS = 60
# Shared by all requests in this GPU process; never reset per sentence.
AUDIO_METRIC_LIMIT = 2048
_audio_metric_rows = 0
_audio_metric_lock = threading.Lock()


class AudioMetrics:
    """Opt-in numeric metadata only; never text, PCM, reference or addresses.

    RTF is generation wall/audio duration. Queue wait is reported separately
    so a full output queue is not mistaken for slow model generation.
    """
    def __init__(self, request_id, *, clock=time.monotonic, write=print):
        self.request_id, self.clock, self.write = request_id, clock, write
        self.started = self.last = clock()
        self.samples = self.sequence = 0
        self.queue_wait = 0.0
        self.generation_wall = 0.0
        self.lock = threading.Lock()

    def event(self, event, *, size=0, queue_chunks=0, wait_seconds=0.0):
        global _audio_metric_rows
        with _audio_metric_lock:
            if _audio_metric_rows >= AUDIO_METRIC_LIMIT:
                return
            _audio_metric_rows += 1
        with self.lock:
            self._event(event, size=size, queue_chunks=queue_chunks, wait_seconds=wait_seconds)

    def _event(self, event, *, size, queue_chunks, wait_seconds):
        now = self.clock()
        if event == 'gpu_chunk':
            self.samples += size // 2
            self.sequence += 1
        self.queue_wait += wait_seconds
        audio = self.samples / 48000
        if event == 'gpu_chunk':
            self.generation_wall = max(0.0, now - self.started - self.queue_wait)
        generation = self.generation_wall  # EOF/drain time is not model inference.
        row = {'audio_metric': event, 'request_id': self.request_id,
               'sequence': self.sequence, 'elapsed_seconds': now - self.started,
               'chunk_bytes': size, 'gap_seconds': now - self.last,
               'queue_chunks': queue_chunks, 'queue_wait_seconds': wait_seconds,
               'queue_wait_total_seconds': self.queue_wait,
               'generated_audio_seconds': audio, 'generation_wall_seconds': generation,
               'generation_rtf': generation / audio if audio else None}
        if event == 'gpu_chunk':
            self.last = now
        try:
            self.write(json.dumps(row, allow_nan=False), flush=True)
        except Exception:
            pass  # Observation cannot break synthesis.


def audio_metrics(request_id):
    if os.getenv('ANGELENO_AUDIO_METRICS') != '1':
        return None
    tag = request_id if isinstance(request_id, str) and re.fullmatch(r'[a-f0-9]{32}', request_id) else uuid.uuid4().hex
    return AudioMetrics(tag)


def generate_stream(candidate, text, reference):
    # Startup must exercise the same inference path as user speech.
    return candidate.generate_streaming(
        text=text, reference_wav_path=str(reference),
        cfg_value=2.0, inference_timesteps=10,
        normalize=False, denoise=False, retry_badcase=False,
    )


async def warmup(candidate, reference):
    done = threading.Event()
    stop = threading.Event()
    succeeded = []
    if not generation_lock.acquire(blocking=False):
        raise RuntimeError('GPU busy during startup warmup')

    def generate():
        stream = None
        ok = False
        try:
            stream = generate_stream(candidate, '你好。', reference)
            samples = 0
            for wav in stream:
                if stop.is_set():
                    return
                audio = np.asarray(wav)
                if not np.isfinite(audio).all():
                    raise ValueError('Invalid warmup audio')
                samples += audio.size
            ok = samples > 0
        except Exception:
            # No model exception, reference path or generated audio in health.
            ok = False
        finally:
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                ok = False
            finally:
                succeeded.append(ok)
                generation_lock.release()
                done.set()

    thread = threading.Thread(target=generate, daemon=True)
    try:
        thread.start()
    except BaseException:
        generation_lock.release()
        raise

    async def wait():
        while not done.is_set():
            await asyncio.sleep(0.01)

    # A stuck CUDA call cannot safely be killed in a Python thread. Fail the
    # ASGI startup; never publish this candidate or accept a user request.
    try:
        await asyncio.wait_for(wait(), timeout=WARMUP_TIMEOUT_SECONDS)
    finally:
        stop.set()
    if not succeeded[0]:
        raise RuntimeError('Voice startup warmup failed')


@asynccontextmanager
async def lifespan(app):
    global model, reference_state
    model = None
    reference_state = None
    reference = configured_reference()
    model_path = Path(os.environ.get('VOXCPM_MODEL', ''))
    if not os.environ.get('VOXCPM_MODEL') or not model_path.is_dir():
        raise ValueError('VOXCPM_MODEL must explicitly name an already downloaded local model directory')
    reference_metadata = validate_reference_asset(reference)
    from voxcpm import VoxCPM
    candidate = VoxCPM.from_pretrained(
        str(model_path),
        device='cuda', load_denoiser=False,
        optimize=os.getenv('VOXCPM_COMPILE', '0') == '1',
    )
    if candidate.tts_model.sample_rate != 48000:
        raise RuntimeError('This endpoint requires VoxCPM2 48 kHz output')
    await warmup(candidate, reference)
    model = candidate
    reference_state = reference_metadata
    try:
        yield
    finally:
        model = None
        reference_state = None


def configured_reference():
    value = os.environ.get('VOXCPM_REFERENCE', '')
    if not value or not Path(value).is_file():
        raise ValueError('VOXCPM_REFERENCE must explicitly name a private reference audio file')
    return Path(value)


def validate_reference_asset(reference):
    """Bind an operator-selected asset; never infer a person from its filename.

    Digest validation proves the configured file, not who is speaking in it.
    Diagnostic purpose is explicit and cannot satisfy the Jo worker contract.
    """
    purpose = os.getenv('VOXCPM_REFERENCE_PURPOSE', '')
    if purpose not in {'jo', 'diagnostic'}:
        raise ValueError('Explicit VOXCPM_REFERENCE_PURPOSE=jo or diagnostic is required')
    asset_id = os.getenv('VOXCPM_REFERENCE_ID', '')
    expected = os.getenv('VOXCPM_REFERENCE_SHA256', '')
    if purpose == 'jo' and (not asset_id or not expected):
        raise ValueError('Jo requires explicit VOXCPM_REFERENCE_ID and VOXCPM_REFERENCE_SHA256 from the selected asset')
    if asset_id and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}', asset_id):
        raise ValueError('VOXCPM_REFERENCE_ID must be a logical asset ID, not a path')
    if expected and not re.fullmatch(r'[a-fA-F0-9]{64}', expected):
        raise ValueError('VOXCPM_REFERENCE_SHA256 must be an explicit64hex digest')
    try:
        with reference.open('rb') as source:
            digest = None
            if expected:
                checksum = hashlib.sha256()
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    checksum.update(block)
                digest = checksum.hexdigest()
    except OSError:
        raise ValueError('Selected reference asset is not readable; no fallback voice used') from None
    if expected and digest != expected.lower():
        raise ValueError('Selected reference does not match the declared asset digest; no fallback voice used')
    return {'purpose': purpose, 'asset_id': asset_id or 'diagnostic',
            'sha256': digest, 'verified': bool(expected)}


app = FastAPI(lifespan=lifespan)


class Speech(BaseModel):
    input: str = Field(min_length=1, max_length=2000)
    voice: str = 'jo'


@app.get('/health')
def health():
    return {'ready': model is not None, 'model': 'VoxCPM2',
            'backend': 'official-pytorch', 'sample_rate': 48000,
            'busy': generation_lock.locked(),
            'reference_purpose': reference_state['purpose'] if reference_state else None,
            'reference_asset_id': reference_state['asset_id'] if reference_state else None,
            'reference_sha256': reference_state['sha256'] if reference_state else None,
            'reference_verified': bool(reference_state and reference_state['verified'])}


@app.post('/v1/audio/speech/stream')
async def speech(request: Speech, x_angeleno_audio_request: str | None = Header(default=None)):
    if request.voice != 'jo':
        raise HTTPException(400, 'Only the configured Jo reference is available')
    if model is None:
        raise HTTPException(503, 'Voice model is not ready')
    try:
        reference = configured_reference()
    except ValueError as exc:
        raise HTTPException(503, 'Reference audio has not been configured') from exc
    chunks = queue.Queue(maxsize=4)
    stop = threading.Event()
    metrics = audio_metrics(x_angeleno_audio_request)

    def put(item):
        before = time.monotonic() if metrics else None
        while not stop.is_set():
            try:
                chunks.put(item, timeout=0.1)
                if metrics:
                    metrics.event('gpu_queued', queue_chunks=chunks.qsize(),
                                  wait_seconds=time.monotonic() - before)
                return
            except queue.Full:
                pass

    def generate():
        stream = None
        try:
            stream = generate_stream(model, request.input, reference)
            for wav in stream:
                if stop.is_set():
                    break
                pcm = (np.clip(wav, -1, 1) * 32767).astype('<i2').tobytes()
                if metrics:
                    metrics.event('gpu_chunk', size=len(pcm), queue_chunks=chunks.qsize())
                put(pcm)
            put(None)
        except Exception as exc:
            put(exc)
        finally:
            try:
                if stream is not None:
                    stream.close()
            finally:
                generation_lock.release()

    async def stream_audio():
        try:
            while True:
                try:
                    item = chunks.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue
                if item is None:
                    if metrics:
                        metrics.event('gpu_eof', queue_chunks=chunks.qsize())
                    break
                if isinstance(item, Exception):
                    raise item
                if metrics:
                    metrics.event('gpu_dequeued', size=len(item), queue_chunks=chunks.qsize())
                yield item
        finally:
            # Never replay a partially emitted sentence on disconnect.
            stop.set()

    class GPUResponse(StreamingResponse):
        async def __call__(self, scope, receive, send):
            # Reserve the GPU only when ASGI starts serving the response. Its
            # lifetime includes cancellation before the first body iteration.
            if not generation_lock.acquire(blocking=False):
                await JSONResponse({'detail': 'GPU busy; retry shortly'}, status_code=503)(scope, receive, send)
                return
            try:
                threading.Thread(target=generate, daemon=True).start()
            except BaseException:
                generation_lock.release()
                raise
            try:
                await super().__call__(scope, receive, send)
            finally:
                stop.set()

    return GPUResponse(stream_audio(), media_type='audio/pcm',
                       headers={'X-Sample-Rate': '48000',
                                'X-Reference-Purpose': reference_state['purpose'] if reference_state else 'unknown'})


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('VOXCPM_PORT', '18080')))
