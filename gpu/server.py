"""Angeleno PCM endpoint, backed by official VoxCPM2 on CUDA.

One local GPU, one active synthesis. No denoiser, no cloud reference upload.
Bind to loopback and reach it through SSH. Raw audio is 48 kHz mono s16le.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import queue
import threading

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('HF_HOME', str(ROOT.parent / 'private/cache'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
model = None
generation_lock = threading.Lock()
WARMUP_TIMEOUT_SECONDS = 60


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
    global model
    model = None
    reference = configured_reference()
    model_path = Path(os.environ.get('VOXCPM_MODEL', ''))
    if not os.environ.get('VOXCPM_MODEL') or not model_path.is_dir():
        raise ValueError('VOXCPM_MODEL must explicitly name an already downloaded local model directory')
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
    try:
        yield
    finally:
        model = None


def configured_reference():
    value = os.environ.get('VOXCPM_REFERENCE', '')
    if not value or not Path(value).is_file():
        raise ValueError('VOXCPM_REFERENCE must explicitly name a private reference audio file')
    return Path(value)


app = FastAPI(lifespan=lifespan)


class Speech(BaseModel):
    input: str = Field(min_length=1, max_length=2000)
    voice: str = 'jo'


@app.get('/health')
def health():
    return {'ready': model is not None, 'model': 'VoxCPM2',
            'backend': 'official-pytorch', 'sample_rate': 48000,
            'busy': generation_lock.locked()}


@app.post('/v1/audio/speech/stream')
async def speech(request: Speech):
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

    def put(item):
        while not stop.is_set():
            try:
                chunks.put(item, timeout=0.1)
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
                put((np.clip(wav, -1, 1) * 32767).astype('<i2').tobytes())
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
                    break
                if isinstance(item, Exception):
                    raise item
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
                       headers={'X-Sample-Rate': '48000'})


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('VOXCPM_PORT', '18080')))
