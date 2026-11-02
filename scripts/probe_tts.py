#!/usr/bin/env python3
"""Plan by default; --run --url --output explicitly probes a loopback TTS service.

Public fixed inputs only. No env/config/persona/ledger loading, provider calls,
server startup, synthesis retries, playback or automatic resumption.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
INPUTS = ROOT / 'examples/tts-probe'
NAMES = ('normal', 'cancel', 'recovery')


class ProbeError(ValueError):
    """A probe-owned diagnostic; never constructed from a response body."""


def plan():
    cases = []
    for name in NAMES:
        path = INPUTS / f'{name}.json'
        raw = path.read_bytes()
        payload = json.loads(raw)
        if set(payload) != {'input', 'voice'} or payload['voice'] != 'jo' or not 1 <= len(payload['input']) <= 2000:
            raise ProbeError('Invalid public probe input')
        cases.append({'name': name, 'input_sha256': hashlib.sha256(raw).hexdigest(),
                      'payload': payload, 'status': 'not_run'})
    return {'mode': 'tts_transport_probe', 'status': 'not_run', 'cases': cases,
            'human_heard': None, 'server_cancel_acknowledged': None,
            'browser_playback_verified': False, 'model_calls': 0}


def endpoint(value):
    parsed = urlsplit(value)
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = False
    if (parsed.scheme not in {'http', 'https'} or not local or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
        raise ProbeError('Explicit loopback HTTP(S) base URL required; use an already authorized route')
    return value.rstrip('/')


class Receipt:
    def __init__(self, output, report):
        self.output, self.report = output, report

    def save(self):
        temporary = self.output / 'receipt.json.next'
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + '\n')
        temporary.chmod(0o600)
        temporary.replace(self.output / 'receipt.json')


async def health(session, url):
    async with session.get(url + '/health', allow_redirects=False) as response:
        if response.status != 200:
            raise ProbeError(f'Health HTTP {response.status}')
        data = await response.json()
    if data.get('ready') is not True or data.get('sample_rate') != 48000 or type(data.get('busy')) is not bool:
        raise ProbeError('Health must report ready, 48000 Hz and explicit busy state')
    return {key: data[key] for key in ('ready', 'sample_rate', 'busy')}


async def collect(session, url, case, receipt, cancel_bytes):
    from worker.pcm import PCMResampler
    case.update(status='started', pcm_bytes=0, first_pcm_ms=None, ended_ms=None,
                client_closed_ms=None, eof=False, client_cancelled=False,
                pcm_path=f"{case['name']}.pcm")
    receipt.save()
    started = time.monotonic()
    elapsed = lambda: round((time.monotonic() - started) * 1000, 3)
    converter = PCMResampler(48000, 48000)
    with (receipt.output / case['pcm_path']).open('xb') as audio:
        (receipt.output / case['pcm_path']).chmod(0o600)
        async with session.post(url + '/v1/audio/speech/stream', json=case['payload'],
                                allow_redirects=False) as response:
            case['http_status'] = response.status
            if response.status != 200:
                raise ProbeError(f"{case['name']} HTTP {response.status}; not retried")
            content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            rate = response.headers.get('X-Sample-Rate', '')
            # Keep only protocol facts, never arbitrary header text or bodies.
            case['response_content_type'] = 'audio/pcm' if content_type == 'audio/pcm' else 'unexpected'
            case['response_sample_rate'] = int(rate) if rate.isascii() and rate.isdecimal() and len(rate) <= 6 else None
            if content_type != 'audio/pcm' or rate != '48000':
                raise ProbeError('Expected audio/pcm and X-Sample-Rate: 48000')
            async for chunk in response.content.iter_chunked(4096):
                pcm = converter.push(chunk)
                if not pcm:
                    continue
                if case['first_pcm_ms'] is None:
                    case['first_pcm_ms'] = elapsed()
                audio.write(pcm)
                audio.flush()
                case['pcm_bytes'] += len(pcm)
                receipt.save()
                if case['name'] == 'cancel' and case['pcm_bytes'] >= cancel_bytes:
                    response.close()
                    case.update(client_cancelled=True, client_closed_ms=elapsed())
                    break
            else:
                try:
                    converter.finish()
                except ValueError:
                    raise ProbeError('Truncated 16-bit PCM sample') from None
                case['eof'] = True
    case['ended_ms'] = elapsed()
    case['pcm_sha256'] = hashlib.sha256((receipt.output / case['pcm_path']).read_bytes()).hexdigest()
    if case['pcm_bytes'] == 0:
        raise ProbeError('No complete PCM samples received')
    if case['name'] == 'cancel' and not case['client_cancelled']:
        raise ProbeError('Stream ended before cancellation threshold; cancellation not exercised')
    case['status'] = 'client_closed' if case['client_cancelled'] else 'stream_completed'
    receipt.save()


async def run_probe(report, url, output, *, timeout=30, cancel_bytes=9600, session_factory=None):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ProbeError('Timeout must be finite and positive')
    # Import network support only after explicit CLI authorization/target checks.
    import aiohttp
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    receipt = Receipt(output, report)
    report.update(status='started', url=url, cancel_after_pcm_bytes=cancel_bytes,
                  request_timeout_seconds=timeout, health_observations=[])
    receipt.save()
    active = None
    try:
        factory = session_factory or (lambda: aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(force_close=True), trust_env=False,
            timeout=aiohttp.ClientTimeout(total=timeout)))
        async with factory() as session:
            for case in report['cases']:
                active = case
                async with asyncio.timeout(timeout):
                    before = await health(session, url)
                report['health_observations'].append({'phase': 'before_' + case['name'], **before})
                receipt.save()
                if before['busy']:
                    raise ProbeError('Service already busy; no synthesis sent')
                async with asyncio.timeout(timeout):
                    await collect(session, url, case, receipt, cancel_bytes)
                if case['name'] == 'cancel':
                    case.update(post_cancel_busy_seen=False, post_cancel_idle_observed=None)
                    released_at = time.monotonic()
                    async with asyncio.timeout(timeout):
                        while True:
                            observed = await health(session, url)
                            report['health_observations'].append({'phase': 'after_cancel', **observed})
                            receipt.save()
                            if observed['busy']:
                                case['post_cancel_busy_seen'] = True
                            if not observed['busy']:
                                case['post_cancel_idle_observed'] = True
                                case['busy_false_after_cancel_observation_start_ms'] = round(
                                    (time.monotonic() - released_at) * 1000, 3)
                                receipt.save()
                                break
                            await asyncio.sleep(0.1)
            report['status'] = 'transport_sequence_completed'
    except BaseException as exc:
        report.update(status='interrupted' if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else 'failed',
                      error_type=type(exc).__name__,
                      error_reason=str(exc) if isinstance(exc, ProbeError) else None)
        if active is not None and active['status'] in {'not_run', 'started'}:
            active['status'] = report['status']
        raise
    finally:
        receipt.save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--url', help='Explicit loopback TTS base URL; no default')
    parser.add_argument('--output', type=Path, help='New directory inside checkout private/ or tmp/')
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args(argv)
    if not args.run:
        if args.url or args.output:
            parser.error('--url and --output require --run; no request was sent')
        print(json.dumps(plan(), ensure_ascii=False))
        return 0
    if not args.url or args.output is None or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--run requires --url, a new --output directory, and finite positive --timeout')
    try:
        url = endpoint(args.url)
        output = args.output.resolve()
        if not any(output.is_relative_to(ROOT / allowed) and output != ROOT / allowed for allowed in ('private', 'tmp')):
            raise ProbeError('Output must be a new directory inside checkout private/ or tmp/')
        asyncio.run(run_probe(plan(), url, output, timeout=args.timeout))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'Probe stopped: {type(exc).__name__}; preserve receipt and PCM, do not replay automatically', file=sys.stderr)
        return 1
    print(json.dumps({'status': 'transport_sequence_completed', 'receipt': str(output / 'receipt.json'), 'human_heard': None}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
