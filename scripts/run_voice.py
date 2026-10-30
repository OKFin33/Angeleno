#!/usr/bin/env python3
"""Standalone voice launcher: offline check by default, explicit start only."""
from __future__ import annotations

import argparse
import json
import ipaddress
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from her_agent.baseline import configured_pack
from her_agent.config import load_env, resolve_path
from worker.audio_budget import asr_settings, is_loopback


def check_configuration(env, *, root=ROOT, starting=False):
    """Inspect local files/settings only. Never open an endpoint or create state."""
    errors = []
    for name in ('ANGELENO_PAID_ENABLED', 'ANGELENO_MEMORY_CREATE', 'ANGELENO_MEMORY_TOOLS', 'LIVEKIT_FORCE_TCP'):
        if env.get(name, '1' if name == 'LIVEKIT_FORCE_TCP' else '0') not in {'0', '1'}:
            errors.append(f'{name} must be 0 or 1')
    if starting and env.get('ANGELENO_PAID_ENABLED', '0') != '1':
        errors.append('ANGELENO_PAID_ENABLED=1 is required to start voice')
    if env.get('ANGELENO_AUTHOR_VARIANT', 'guided') not in {'raw', 'guided'}:
        errors.append('ANGELENO_AUTHOR_VARIANT must be raw or guided')
    user = env.get('ANGELENO_MEMORY_USER', '')
    if not re.fullmatch(r'[a-z0-9_-]{1,64}', user):
        errors.append('ANGELENO_MEMORY_USER must explicitly select an identity (lowercase letters, digits, _ or -)')
    try:
        pack = configured_pack(env, root=root)
        directory = resolve_path(env.get('ANGELENO_DATA_DIR', 'private/state'), root=root)
        if not directory.is_relative_to((Path(root) / 'private').resolve()):
            errors.append('ANGELENO_DATA_DIR must remain inside this repository private directory')
        elif user and env.get('ANGELENO_MEMORY_CREATE', '0') != '1':
            if not (directory / pack.id / user / 'memory.sqlite3').is_file():
                errors.append('Memory identity does not exist; select an existing identity or explicitly set ANGELENO_MEMORY_CREATE=1')
    except (ValueError, OSError, KeyError) as exc:
        errors.append(f'Author configuration: {exc}')
    for name in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET'):
        if not env.get(name, '').strip():
            errors.append(f'{name} is required')
    for name, schemes in (('LIVEKIT_URL', {'ws', 'wss'}), ('VOXCPM_URL', {'http', 'https'})):
        value = urlparse(env.get(name, ''))
        if value.scheme not in schemes or not value.hostname or value.username or value.password:
            errors.append(f'{name} must be an explicit endpoint URL without embedded credentials')
    try:
        node_ip = ipaddress.ip_address(env.get('LIVEKIT_NODE_IP', '127.0.0.1'))
    except ValueError:
        errors.append('LIVEKIT_NODE_IP must be an explicit IP address')
        node_ip = None
    public_url = env.get('LIVEKIT_PUBLIC_URL', '').strip()
    if public_url:
        public = urlparse(public_url)
        if public.scheme not in {'ws', 'wss'} or not public.hostname or public.username or public.password:
            errors.append('LIVEKIT_PUBLIC_URL must be a WebSocket URL without embedded credentials')
    if public_url or (node_ip is not None and not node_ip.is_loopback) or not is_loopback(env.get('LIVEKIT_URL', '')):
        if env.get('LIVEKIT_API_KEY') == 'devkey' or len(env.get('LIVEKIT_API_SECRET', '')) < 32:
            errors.append('Private device access requires a non-demo LiveKit key and a secret of at least 32 characters')
    for name, default in (('ANGELENO_WEB_PORT', '8767'), ('LIVEKIT_WORKER_PORT', '8081')):
        try:
            if not 1 <= int(env.get(name, default)) <= 65535:
                raise ValueError()
        except ValueError:
            errors.append(f'{name} must be a port between 1 and 65535')
    if env.get('ANGELENO_WEB_PORT', '8767') == env.get('LIVEKIT_WORKER_PORT', '8081'):
        errors.append('Web and worker ports must differ')
    try:
        asr_settings(env, root=root)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        from her_agent.provider import validate_provider_configuration
        # Paid-off remains the safe default. Configuration checks may validate
        # the prospective paid settings without constructing a provider.
        validate_provider_configuration({**env, 'ANGELENO_PAID_ENABLED': '1'} if not starting else env, root=root)
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def require_ports_free(ports):
    """Fail on occupied ports; never find or kill their owners."""
    sockets = []
    try:
        for port, kind in ports:
            listener = socket.socket(socket.AF_INET, kind)
            sockets.append(listener)
            listener.bind(('127.0.0.1', int(port)))
    except OSError as exc:
        raise ValueError('A required local port is in use; choose another port or stop its owner yourself') from exc
    finally:
        for listener in sockets:
            listener.close()


def stop_children(children):
    # Each child was started in its own process group. Never act on a port's PID.
    for child in reversed(children):
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for child in reversed(children):
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)


def run(env, *, livekit_bin=None):
    errors = check_configuration(env, starting=True)
    if errors:
        raise ValueError('; '.join(errors))
    ports = [(int(env.get('ANGELENO_WEB_PORT', '8767')), socket.SOCK_STREAM),
             (int(env.get('LIVEKIT_WORKER_PORT', '8081')), socket.SOCK_STREAM)]
    executable = None
    if livekit_bin:
        endpoint = urlparse(env['LIVEKIT_URL'])
        if not is_loopback(env['LIVEKIT_URL']) or endpoint.scheme != 'ws':
            raise ValueError('--livekit-bin requires a loopback ws:// LIVEKIT_URL')
        executable = shutil.which(livekit_bin)
        if not executable:
            raise ValueError('The explicitly selected livekit-server executable is unavailable')
        ports.extend([(endpoint.port or 7880, socket.SOCK_STREAM),
                      (7881, socket.SOCK_STREAM), (7882, socket.SOCK_DGRAM)])
    require_ports_free(ports)
    # Opening validates identity/seed compatibility before registering workers.
    from her_agent.voice import validate_voice_setup
    validate_voice_setup(env)
    private = ROOT / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    children = []
    try:
        with tempfile.TemporaryDirectory(prefix='voice-run-', dir=private) as directory:
            commands = []
            if executable:
                config = {'port': urlparse(env['LIVEKIT_URL']).port or 7880,
                          'bind_addresses': ['127.0.0.1'],
                          'rtc': {'tcp_port': 7881, 'udp_port': 7882,
                                  'force_tcp': env.get('LIVEKIT_FORCE_TCP', '1') == '1', 'use_external_ip': False,
                                  'node_ip': env.get('LIVEKIT_NODE_IP', '127.0.0.1'), 'advertise_internal_ip': True,
                                  'enable_loopback_candidate': True},
                          'keys': {env['LIVEKIT_API_KEY']: env['LIVEKIT_API_SECRET']},
                          'logging': {'level': 'warn'}}
                config_path = Path(directory) / 'livekit.json'
                with open(config_path, 'w', opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
                    json.dump(config, file)
                commands.append([executable, '--config', str(config_path)])
            commands.extend([[sys.executable, '-m', 'worker.web_server'],
                             [sys.executable, '-m', 'worker.main', 'start']])
            try:
                for command in commands:
                    children.append(subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True))
                print(f"Angeleno: http://127.0.0.1:{env.get('ANGELENO_WEB_PORT', '8767')} — Ctrl-C stops this launch.", flush=True)
                while all(child.poll() is None for child in children):
                    time.sleep(0.25)
                raise RuntimeError('A voice component stopped; the remaining launch components were stopped')
            finally:
                stop_children(children)
    except KeyboardInterrupt:
        return


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('check', 'start'), default='check')
    parser.add_argument('--env-file', help='Explicit settings file (environment still takes precedence)')
    parser.add_argument('--livekit-bin', help='On start, also launch this installed LiveKit executable on loopback')
    args = parser.parse_args(argv)
    try:
        env = load_env(env_file=args.env_file)
        if args.env_file:
            env['ANGELENO_ENV_FILE'] = str(resolve_path(args.env_file))
        if args.action == 'check':
            errors = check_configuration(env)
            print(json.dumps({'ready': not errors, 'mode': 'offline configuration only',
                              'paid_enabled': env.get('ANGELENO_PAID_ENABLED', '0') == '1',
                              'errors': errors}, ensure_ascii=False, indent=2))
            return 1 if errors else 0
        def stop_on_signal(signum, frame):
            raise KeyboardInterrupt()
        old_handler = signal.signal(signal.SIGTERM, stop_on_signal)
        try:
            run(env, livekit_bin=args.livekit_bin)
        finally:
            signal.signal(signal.SIGTERM, old_handler)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'Voice setup: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
