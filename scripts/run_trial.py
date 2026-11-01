#!/usr/bin/env python3
"""Bounded private trial. Offline check by default; --run owns only media children.

Adapted from the existing operator full_voice_session supervisor. Device routes,
the installed binary receipt and credentials remain local operator inputs. This
entry never starts GPU/SSH, captures a microphone or installs persistent routes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
from http.server import ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from her_agent.config import load_env, resolve_path
from scripts.run_voice import check_configuration, stop_children


HTTP_READ_TIMEOUT = 2.0
HTTP_CLOSE_TIMEOUT = 1.0


class TrialHTTPServer(ThreadingHTTPServer):
    # A stalled client must neither serialize other requests nor hold shutdown.
    daemon_threads = True
    block_on_close = False

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(HTTP_READ_TIMEOUT)
        return request, address


def start_http(server, stop):
    entered = threading.Event()
    errors = []

    def serve():
        if stop.is_set():
            return
        entered.set()
        try:
            server.serve_forever(poll_interval=.1)
        except Exception as exc:
            errors.append(type(exc).__name__)
            stop.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()  # if this fails, caller must not call BaseServer.shutdown
    return entered, errors


def bounded_http_close(action, label):
    """Never let a stdlib shutdown wait block independent owned-resource cleanup."""
    errors = []

    def call():
        try:
            action()
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=call, daemon=True)
    try:
        thread.start()
        thread.join(HTTP_CLOSE_TIMEOUT)
    except Exception as exc:
        return [type(exc).__name__]
    return [label + 'Timeout'] if thread.is_alive() else errors


def trial_environment(settings, projection, mode, *, inherited=None):
    if mode not in {'technical', 'product'}:
        raise ValueError('Unknown trial mode')
    inherited = dict(os.environ if inherited is None else inherited)
    if mode == 'product' and any(k.startswith('ANGELENO_TRIAL_')
                               for source in (settings, projection, inherited) for k in source):
        raise ValueError('Product trial forbids test hooks')
    if any(settings.get(k, '0') != '0' for k in ('ANGELENO_PAID_ENABLED', 'ANGELENO_WEB_ENABLED')):
        raise ValueError('Keep persistent paid/Web defaults off; opt in by trial mode')
    env = {**inherited, **settings, **projection}
    for key in list(env):
        if key.startswith('ANGELENO_TRIAL_'):
            del env[key]
    env.update(ANGELENO_PAID_ENABLED='1', ANGELENO_MEMORY_CREATE='0',
               ANGELENO_PLACE_TOOLS='0', ANGELENO_WEB_PORT='8767',
               LIVEKIT_WORKER_PORT='8081', ANGELENO_WEB_ENABLED='1' if mode == 'product' else '0')
    if mode == 'product':
        # Current reasonable-use account authorization, not a fresh batch or
        # permission inferred from a historical counter. All rows stay intact.
        env.update(ANGELENO_WEB_EGRESS='public-fields-v1', ANGELENO_WEB_REQUEST_LIMIT='account')
    return env


def check_state(env, *, root=ROOT):
    """Read existing identity/allowance/account metadata without creating state."""
    from her_agent.baseline import configured_pack
    from her_agent.budget import Ledger
    from her_agent.memory import APPLICATION_ID, seed_digest
    from her_agent.provider import _provider_configuration
    from worker.audio_budget import asr_settings
    errors = check_configuration(env, root=root, starting=True)
    if errors:
        raise ValueError('Voice configuration invalid: ' + '; '.join(errors))
    key, _, prices, ledger = _provider_configuration(env, root)
    Ledger.validate_existing(ledger, prices, minimum_balance=env['ANGELENO_MIN_BALANCE'],
                            carryover=env['ANGELENO_BUDGET_CARRYOVER'], account=key)
    pack = configured_pack(env, root=root)
    memory = resolve_path(env.get('ANGELENO_DATA_DIR', 'private/state'), root=root) / pack.id / env['ANGELENO_MEMORY_USER'] / 'memory.sqlite3'
    if memory.is_symlink() or not memory.is_file():
        raise ValueError('Existing memory identity required')
    with sqlite3.connect(memory.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
            raise ValueError('Existing memory identity invalid')
        expected = {'schema': '2', 'character': pack.id, 'user': env['ANGELENO_MEMORY_USER'], 'seed': seed_digest(pack)}
        if any(db.execute('SELECT value FROM meta WHERE key=?', (k,)).fetchone() != (v,) for k, v in expected.items()):
            raise ValueError('Existing identity/seed mismatch; no migration attempted')
    asr, maximum = asr_settings(env, root=root)
    if asr.is_symlink() or not asr.is_file():
        raise ValueError('Existing ASR allowance required; no reset')
    with sqlite3.connect(asr.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA application_id').fetchone()[0] != 0x414E4741:
            raise ValueError('Existing ASR allowance invalid')
        row = db.execute('SELECT max_seconds,reserved_seconds FROM asr_allowance WHERE id=1').fetchone()
        if not row or row[0] != maximum or not 0 <= row[1] < maximum:
            raise ValueError('Existing ASR allowance differs or is exhausted')
    if env['ANGELENO_WEB_ENABLED'] == '1':
        from her_agent.web_lookup import configured_web_lookup
        # Constructor-only validation; no client/provider, balance or HTTP.
        from types import SimpleNamespace
        actor = SimpleNamespace(budget=SimpleNamespace(ledger=SimpleNamespace(prices=prices)))
        configured_web_lookup(env, actor, pack=pack)


def media_scope(env, plan):
    config = deepcopy(plan['proposed_livekit_config_without_keys'])
    tail = ipaddress.ip_address(env['LIVEKIT_NODE_IP'])
    if tail not in ipaddress.ip_network('100.64.0.0/10'):
        raise ValueError('Explicit existing Tailnet IPv4 required')
    rtc = config['rtc']
    if (config.get('port') != 7880 or config.get('bind_addresses') != ['127.0.0.1']
            or config.get('turn', {}).get('enabled') is not False or 'node_ip' in rtc
            or rtc.get('use_external_ip') is not False or rtc.get('tcp_port') != 0
            or rtc.get('udp_port') != 7882 or rtc.get('force_tcp') is not False
            or rtc.get('port_range_start') != 0 or rtc.get('port_range_end') != 0
            or sorted(rtc['ips']['includes']) != sorted([str(tail) + '/32', '127.0.0.1/32'])
            or rtc.get('stun_servers') != ['127.0.0.1:7882']):
        raise ValueError('Verified private RTC scope required')
    public = urlparse(env.get('LIVEKIT_PUBLIC_URL', ''))
    origin = urlparse(env.get('ANGELENO_WEB_ALLOWED_ORIGINS', ''))
    if (env['LIVEKIT_URL'] != 'ws://127.0.0.1:7880'
            or env['VOXCPM_URL'] != 'http://127.0.0.1:18080'
            or public.scheme != 'wss' or public.port != 9444 or not public.hostname
            or not public.hostname.endswith('.ts.net') or public.username or public.password
            or public.path or public.query or public.fragment
            or origin.scheme != 'https' or origin.hostname != public.hostname or origin.port != 9443
            or origin.username or origin.password or origin.path or origin.query or origin.fragment):
        raise ValueError('Approved loopback endpoints and exact private browser origin required')
    return config, public.hostname, sorted([str(tail) + ':7882', '127.0.0.1:7882'])


def serve_state(cli):
    result = subprocess.run([cli, 'serve', 'status', '--json'], capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError('Serve inventory failed')
    state = json.loads(result.stdout)
    if not isinstance(state, dict):
        raise ValueError('Serve inventory is not an object')
    return state


def check_old(state, before):
    for kind in ('TCP', 'Web', 'Foreground'):
        for key, value in before.get(kind, {}).items():
            if state.get(kind, {}).get(key) != value:
                raise RuntimeError('Existing Serve entry changed')
    if state.get('AllowFunnel', {}) != before.get('AllowFunnel', {}):
        raise RuntimeError('Existing Funnel changed')
    for config in state.get('Foreground', {}).values():
        if any(config.get('AllowFunnel', {}).values()):
            raise RuntimeError('Foreground Funnel forbidden')


def has_route(state, host, port, target):
    return any(c.get('TCP', {}).get(str(port)) == {'HTTPS': True}
               and c.get('Web', {}).get(f'{host}:{port}', {}).get('Handlers', {}).get('/') == {'Proxy': f'http://127.0.0.1:{target}'}
               for c in [state, *state.get('Foreground', {}).values()])


def listeners(pid, kind):
    command = ['/usr/sbin/lsof', '-a', '-p', str(pid), '-nP', '-i' + kind, '-Fpn']
    if kind == 'TCP':
        command += ['-sTCP:LISTEN']
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError('Listener inventory failed')
    return sorted(line[1:] for line in result.stdout.splitlines() if line.startswith('n'))


def preflight(args, *, root=ROOT):
    settings = load_env(environ={}, env_file=args.env_file, root=root)
    plan = json.loads(args.route_plan.read_text())
    env = trial_environment(settings, plan['proposed_nonsecret_project_settings'], args.mode)
    env['ANGELENO_ENV_FILE'] = str(args.env_file.resolve())
    config, host, udp = media_scope(env, plan)
    check_state(env, root=root)
    if not args.livekit_bin.is_file() or hashlib.sha256(args.livekit_bin.read_bytes()).hexdigest() != json.loads(args.binary_receipt.read_text())['binary_sha256']:
        raise ValueError('Installed LiveKit differs from local verified receipt')
    if not args.tailscale_bin.is_file() or not os.access(args.tailscale_bin, os.X_OK):
        raise ValueError('Existing Tailscale executable required')
    baseline = json.loads(args.serve_baseline.read_text())['serve_before']
    for port in (9443, 9444):
        if any(str(port) in c.get('TCP', {}) or any(k.endswith(':' + str(port)) for k in c.get('Web', {}))
               for c in [baseline, *baseline.get('Foreground', {}).values()]):
            raise ValueError('Approved trial ports already present in baseline')
    return env, config, host, udp, baseline


def check_host(cli, baseline):
    """Local status only, before reserving GPU; no listener or paid request."""
    if serve_state(cli) != baseline:
        raise RuntimeError('Serve baseline changed; no route overwritten')
    for port, kind in ((7880, 'TCP'), (7881, 'TCP'), (7882, 'UDP'), (8767, 'TCP'), (8081, 'TCP'), (9443, 'TCP'), (9444, 'TCP')):
        command = ['/usr/sbin/lsof', '-nP', '-i' + kind + ':' + str(port), '-Fp']
        result = subprocess.run(command + (['-sTCP:LISTEN'] if kind == 'TCP' else []), capture_output=True, text=True)
        if result.returncode not in (0, 1) or result.stdout:
            raise RuntimeError('Trial port occupied or inventory unavailable; no owner stopped')


def supervise(args, prepared, *, root=ROOT, stop_event=None):
    root = Path(root).resolve()
    env, config, host, udp, baseline = prepared
    now = datetime.now(timezone.utc)
    until = datetime.fromisoformat(args.stop_at or '')
    if until.utcoffset() != timedelta(0) or not now < until <= now + timedelta(minutes=10):
        raise ValueError('Future UTC stop-at within ten minutes required')
    if not args.output or args.output.exists() or args.output.is_symlink() or not args.output.resolve().is_relative_to(root / 'private'):
        raise ValueError('New private receipt path required')
    cli = str(args.tailscale_bin)
    check_host(cli, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(args.output, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        handle.write('{}\n')
    directory = args.output.parent / (args.output.stem + '-resources')
    directory.mkdir(mode=0o700)  # exclusive, never reuse someone else's files
    config_path = directory / 'livekit.json'
    children, logs = [], []
    server = None
    http_entered, http_errors = None, []
    stop = threading.Event() if stop_event is None else stop_event
    old_signals = {}
    report = {'status': 'starting', 'mode': args.mode, 'stop_at': until.isoformat(),
              'supervisor_pid': os.getpid(), 'owned_children': [], 'serve_before': baseline,
              'web_enabled': env['ANGELENO_WEB_ENABLED'] == '1',
              'web_policy': env.get('ANGELENO_WEB_REQUEST_LIMIT') if args.mode == 'product' else None,
              'persistent_configuration_changed': False, 'owns_GPU_SSH': False,
              'purpose': 'bounded private product trial with actor-selected public lookup' if args.mode == 'product' else 'bounded voice-only technical trial',
              'readiness': 'listeners are not proof of complete voice/Web/browser operation'}

    def save():
        args.output.write_text(json.dumps(report, indent=2) + '\n')

    def launch(role, command):
        if stop.is_set() or datetime.now(timezone.utc) >= until:
            raise InterruptedError('Trial stopped before launch')
        log = open(directory / (role + '.log'), 'x', opener=lambda p, f: os.open(p, f, 0o600))
        logs.append(log)
        child = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        children.append(child)
        report['owned_children'].append({'role': role, 'pid': child.pid})
        save()
        return child

    def wait_for(child, predicate, seconds):
        deadline = time.monotonic() + seconds
        while not predicate():
            if stop.is_set() or datetime.now(timezone.utc) >= until:
                raise InterruptedError('Trial stopped during startup')
            if child.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Owned component failed readiness')
            time.sleep(.1)

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_signals[sig] = signal.signal(sig, lambda *_: stop.set())
        config.update(keys={env['LIVEKIT_API_KEY']: env['LIVEKIT_API_SECRET']}, logging={'level': 'info'}, room={'auto_create': False})
        with open(config_path, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
            json.dump(config, handle)
        child = launch('livekit', [str(args.livekit_bin), '--config', str(config_path)])
        wait_for(child, lambda: listeners(child.pid, 'TCP') == ['127.0.0.1:7880'], 15)
        if listeners(child.pid, 'UDP') != udp:
            raise RuntimeError('Actual RTC listener scope mismatch')
        from worker.web_server import handler_for
        server = TrialHTTPServer(('127.0.0.1', 8767), handler_for(env))
        http_entered, http_errors = start_http(server, stop)
        worker = launch('worker', [sys.executable, str(root / 'scripts/run_trial.py'), '_worker'])
        wait_for(worker, lambda: listeners(worker.pid, 'TCP') == ['127.0.0.1:8081'], 20)
        for port, target in ((9443, 8767), (9444, 7880)):
            route = launch('serve-' + str(port), [cli, 'serve', '--https=' + str(port), 'http://127.0.0.1:' + str(target)])

            def routed():
                state = serve_state(cli)
                check_old(state, baseline)
                return has_route(state, host, port, target)

            wait_for(route, routed, 20)
        report['status'] = 'listeners_up'; save()
        print(json.dumps({'status': 'listeners_up', 'mode': args.mode, 'receipt': str(args.output)}), flush=True)
        while datetime.now(timezone.utc) < until and not stop.wait(.2):
            if any(child.poll() is not None for child in children):
                raise RuntimeError('Owned component exited')
    except BaseException as exc:
        report.update(status='incomplete', error_type=type(exc).__name__)
    finally:
        stop.set()
        errors = list(http_errors)
        report['http_loop_entered'] = http_entered is not None and http_entered.is_set()
        # shutdown() deadlocks if serve_forever never ran. Even after entry,
        # bound it in a daemon helper; no HTTP failure may gate PID/key cleanup.
        if server and report['http_loop_entered']:
            errors.extend(bounded_http_close(server.shutdown, 'HTTPShutdown'))
        for child in reversed(children):
            try:
                stop_children([child])
            except Exception as exc:
                errors.append(type(exc).__name__)
        for log in logs:
            log.close()
        try:
            config_path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(type(exc).__name__)
        if server:
            errors.extend(bounded_http_close(server.server_close, 'HTTPServerClose'))
        for sig, previous in old_signals.items():
            signal.signal(sig, previous)
        report['owned_children_stopped'] = all(c.poll() is not None for c in children)
        report['temporary_key_config_removed'] = not config_path.exists()
        try:
            report['serve_restored_exactly'] = serve_state(cli) == baseline
        except Exception:
            report['serve_restored_exactly'] = False
        if errors:
            report.update(status='incomplete', cleanup_error_types=errors)
        elif report['status'] != 'incomplete':
            report['status'] = 'stopped'
        save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--check-host', action='store_true', help='Also inspect existing local Serve state and port ownership; starts nothing')
    parser.add_argument('--mode', choices=('technical', 'product'), default='technical')
    parser.add_argument('--stop-at')
    parser.add_argument('--output', type=Path)
    for name, default in (('env-file', 'private/runtime.env'), ('route-plan', 'tmp/nexus-offline/private-media-route-plan.json'),
                          ('serve-baseline', 'tmp/nexus-offline/private-media-preflight.json'),
                          ('livekit-bin', 'private/tools/livekit-server'), ('binary-receipt', 'tmp/nexus-offline/livekit-project-install.json'),
                          ('tailscale-bin', '/Applications/Tailscale.app/Contents/MacOS/Tailscale')):
        parser.add_argument('--' + name, type=Path, default=ROOT / default)
    args = parser.parse_args(argv)
    try:
        prepared = preflight(args)
        if not args.run:
            if args.check_host:
                check_host(str(args.tailscale_bin), prepared[4])
            print(json.dumps({'ready': True, 'mode': args.mode, 'executed': False,
                              'host_inventory_checked': args.check_host,
                              'check': 'files/configuration only; no endpoint/GPU readiness claim'}))
            return 0
        report = supervise(args, prepared)
        return 0 if report['status'] == 'stopped' and report['owned_children_stopped'] and report['serve_restored_exactly'] else 1
    except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error):
        print('Trial configuration or execution failed; no secret exception body emitted', file=sys.stderr)
        return 1


def worker_entry():
    import worker.agent as worker
    worker.AgentServer = partial(worker.AgentServer, host='127.0.0.1')
    sys.argv = [sys.argv[0], 'start']
    worker.main()


if __name__ == '__main__':
    if sys.argv[1:] == ['_worker']:
        worker_entry()
    else:
        raise SystemExit(main())
