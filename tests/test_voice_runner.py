"""Offline launcher and local HTTP boundary tests; no real credentials/services."""
import asyncio
from contextlib import redirect_stdout
from email import message_from_bytes
from http.server import HTTPServer
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from angeleno import launcher as run_voice
from test_memory import pack_at
from angeleno.worker import web_server


def configuration(root):
    pack_at(root)
    return {'ANGELENO_AUTHOR_PACK': str(root / 'pack.json'),
            'ANGELENO_DATA_DIR': str(root / 'private/state'),
            'ANGELENO_MEMORY_USER': 'synthetic', 'ANGELENO_MEMORY_PURPOSE': 'real_user', 'ANGELENO_MEMORY_CREATE': '1',
            'ANGELENO_PAID_ENABLED': '0', 'ANGELENO_ASR_MAX_SECONDS': '5',
            'DEEPSEEK_API_KEY': 'synthetic-key', 'DASHSCOPE_API_KEY': 'synthetic-key',
            'DEEPSEEK_MODEL': 'deepseek-flash',
            'ANGELENO_PRICE_MODEL': 'deepseek-flash', 'ANGELENO_BUDGET_CURRENCY': 'CNY',
            'ANGELENO_PRICE_INPUT': '2', 'ANGELENO_PRICE_CACHE_HIT': '0.04',
            'ANGELENO_PRICE_OUTPUT': '8', 'ANGELENO_PRICE_SOURCE': 'synthetic-pricing-fixture',
            'ANGELENO_MIN_BALANCE': '1', 'ANGELENO_BUDGET_CARRYOVER': '0',
            'LIVEKIT_API_KEY': 'test', 'LIVEKIT_API_SECRET': 'synthetic-secret',
            'LIVEKIT_URL': 'ws://127.0.0.1:7880', 'VOXCPM_URL': 'http://127.0.0.1:18080',
            'VOXCPM_REFERENCE_PURPOSE': 'jo', 'VOXCPM_REFERENCE_ID': 'synthetic-jo',
            'VOXCPM_REFERENCE_SHA256': 'a' * 64}


class RunnerTests(unittest.TestCase):
    def test_default_check_is_local_read_only_even_with_remote_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = configuration(root)
            env['LIVEKIT_API_SECRET'] = 'synthetic-secret-' * 3
            env['LIVEKIT_URL'] = 'wss://example.invalid'
            env['VOXCPM_URL'] = 'https://example.invalid'
            with patch('socket.socket', side_effect=AssertionError('No sockets in config check')), \
                 patch('subprocess.Popen', side_effect=AssertionError('No processes in config check')):
                self.assertEqual(run_voice.check_configuration(env, root=root), [])
            self.assertFalse((root / 'private').exists(), 'Check must not initialize memory or ledgers')
            self.assertIn('ANGELENO_PAID_ENABLED=1', ';'.join(run_voice.check_configuration(env, root=root, starting=True)))

    def test_prefetch_mode_check_is_local_and_rejects_unbounded_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = configuration(root)
            with patch('socket.socket', side_effect=AssertionError('No endpoint probe')), \
                 patch('subprocess.Popen', side_effect=AssertionError('No resource start')):
                self.assertEqual(run_voice.check_configuration({**env, 'ANGELENO_TTS_PREFETCH_FRAMES':'2'}, root=root), [])
                errors = run_voice.check_configuration({**env, 'ANGELENO_TTS_PREFETCH_FRAMES':'99'}, root=root)
                self.assertIn('ANGELENO_TTS_PREFETCH_FRAMES must be 0 or 2', errors)
            self.assertFalse((root / 'private').exists())

    def test_missing_budget_or_asr_allowance_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = configuration(root)
            for key in ('ANGELENO_ASR_MAX_SECONDS', 'ANGELENO_PRICE_INPUT', 'ANGELENO_MIN_BALANCE'):
                with self.subTest(key=key):
                    missing = {k: v for k, v in env.items() if k != key}
                    self.assertTrue(run_voice.check_configuration(missing, root=root))
            self.assertFalse((root / 'private').exists())

    def test_occupied_port_remains_owned_by_original_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch('os.killpg', side_effect=AssertionError('Do not kill port owners')):
                with self.assertRaisesRegex(ValueError, 'in use'):
                    run_voice.require_ports_free([(port, socket.SOCK_STREAM)])
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                connection, _ = listener.accept()
                connection.close()

    def test_cross_device_configuration_rejects_demo_or_weak_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for setting in ({'LIVEKIT_PUBLIC_URL': 'wss://example.invalid'},
                            {'LIVEKIT_NODE_IP': '100.64.0.1'}):
                env = {**configuration(root), **setting}
                self.assertIn('non-demo LiveKit key', ';'.join(run_voice.check_configuration(env, root=root)))
                env['LIVEKIT_API_SECRET'] = 'synthetic-strong-secret-' * 3
                self.assertEqual(run_voice.check_configuration(env, root=root), [])
                env['LIVEKIT_API_KEY'] = 'devkey'
                self.assertIn('non-demo LiveKit key', ';'.join(run_voice.check_configuration(env, root=root)))

    def test_cleanup_targets_only_spawned_process_groups(self):
        own = Mock(pid=87654)
        own.poll.return_value = None
        stopped = Mock(pid=87655)
        stopped.poll.return_value = 0
        with patch('os.killpg') as kill:
            run_voice.stop_children([own, stopped])
        kill.assert_called_once_with(87654, run_voice.signal.SIGTERM)
        own.wait.assert_called_once_with(timeout=5)

    def test_start_refuses_paid_off_before_any_process(self):
        with patch.object(run_voice, 'check_configuration', return_value=['paid off']), \
             patch('subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'paid off'):
                run_voice.run({})
            spawn.assert_not_called()

    def test_partial_launch_failure_cleans_up_previous_child(self):
        child = Mock(pid=87654)
        child.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(run_voice, 'ROOT', Path(tmp)), \
             patch.object(run_voice, 'check_configuration', return_value=[]), \
             patch.object(run_voice, 'require_ports_free'), \
             patch('angeleno.voice.validate_voice_setup'), \
             patch('subprocess.Popen', side_effect=[child, OSError('synthetic spawn failure')]), \
             patch('os.killpg') as kill:
            with self.assertRaisesRegex(OSError, 'synthetic spawn failure'):
                run_voice.run({})
            kill.assert_called_once_with(child.pid, run_voice.signal.SIGTERM)
            self.assertEqual(list((Path(tmp) / 'private/state').iterdir()), [])

    def test_local_livekit_config_is_private_and_start_path_reaches_child(self):
        child = Mock(pid=87654)
        child.poll.return_value = None
        captured = {}
        def spawn(command, **kwargs):
            if command[0] == '/synthetic/livekit-server':
                path = Path(command[2])
                captured['mode'] = path.stat().st_mode & 0o777
                captured['config'] = json.loads(path.read_text())
                return child
            raise OSError('synthetic second child failure')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(run_voice, 'ROOT', Path(tmp)), \
             patch.object(run_voice, 'check_configuration', return_value=[]), \
             patch.object(run_voice, 'require_ports_free'), \
             patch('angeleno.voice.validate_voice_setup'), \
             patch('shutil.which', return_value='/synthetic/livekit-server'), \
             patch('subprocess.Popen', side_effect=spawn), \
             patch('os.killpg') as kill:
            env = {'LIVEKIT_URL': 'ws://127.0.0.1:17883',
                   'LIVEKIT_API_KEY': 'synthetic-local', 'LIVEKIT_API_SECRET': 'synthetic-secret',
                   'LIVEKIT_NODE_IP': '100.64.0.1', 'LIVEKIT_FORCE_TCP': '1'}
            with self.assertRaisesRegex(OSError, 'synthetic second child failure'):
                run_voice.run(env, livekit_bin='livekit-server')
            self.assertEqual(captured['mode'], 0o600)
            self.assertEqual(captured['config']['rtc']['node_ip'], '100.64.0.1')
            self.assertEqual(captured['config']['port'], 17883)
            kill.assert_called_once_with(child.pid, run_voice.signal.SIGTERM)
            self.assertEqual(list((Path(tmp) / 'private/state').iterdir()), [])

    def test_web_health_does_not_probe_and_token_requires_allowed_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = configuration(Path(tmp))
            manifest = Path(tmp) / 'pack.json'
            pack = json.loads(manifest.read_text())
            pack['id'] = 'demo-local'
            manifest.write_text(json.dumps(pack))
            server = HTTPServer(('127.0.0.1', 0), web_server.handler_for(env))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with patch('angeleno.launcher.check_configuration', return_value=[]), \
                     patch.object(web_server, 'create_room_and_token', new_callable=AsyncMock) as create:
                    with urlopen(url + '/healthz', timeout=2) as response:
                        self.assertEqual(json.load(response), {'ready': True, 'scope': 'local-configuration', 'persona_label': 'Synthetic demo (Lin)', 'language':'en'})
                    request = Request(url + '/token', data=b'{}', method='POST')
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request, timeout=2)
                    self.assertEqual(error.exception.code, 403)
                    create.assert_not_called()
                    with self.assertRaises(HTTPError) as error:
                        urlopen(url + '/../private/runtime.env', timeout=2)
                    self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class TokenEndpointTests(unittest.TestCase):
    """Exercise the real HTTP handler and token factory without network/services."""

    def token_request(self, *, public_url=None, origin='http://localhost:8767',
                      host='localhost:8767', signaling='ws://127.0.0.1:7880', paid='1',
                      forwarded=None):
        env = {'LIVEKIT_URL': signaling, 'LIVEKIT_API_KEY': 'synthetic-key',
               'LIVEKIT_API_SECRET': 'synthetic-secret', 'ANGELENO_PAID_ENABLED': paid,
               'ANGELENO_WEB_ALLOWED_ORIGINS': 'https://client.example.invalid'}
        if public_url is not None:
            env['LIVEKIT_PUBLIC_URL'] = public_url
        handler = object.__new__(web_server.handler_for(env))
        handler.path = '/token'
        handler.command, handler.request_version = 'POST', 'HTTP/1.1'
        handler.requestline = 'POST /token HTTP/1.1'
        handler.headers = {'Host': host, 'Content-Length': '2'}
        if origin is not None:
            handler.headers['Origin'] = origin
        handler.headers.update(forwarded or {})
        handler.rfile, handler.wfile = io.BytesIO(b'{}'), io.BytesIO()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.room.create_room = AsyncMock()
        client.agent_dispatch.create_dispatch = AsyncMock()
        token = Mock()
        token.with_identity.return_value = token
        token.with_name.return_value = token
        token.with_grants.return_value = token
        token.to_jwt.return_value = 'synthetic-token-not-a-real-credential'
        with patch('livekit.api.LiveKitAPI', return_value=client) as api, \
             patch('livekit.api.AccessToken', return_value=token) as access_token, \
             patch('socket.socket.connect', side_effect=AssertionError('No network')), \
             patch('subprocess.Popen', side_effect=AssertionError('No services')):
            handler.do_POST()
        head, body = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
        status = int(head.split(b' ', 2)[1])
        headers = message_from_bytes(head.split(b'\r\n', 1)[1])
        return status, headers, json.loads(body), api, access_token, client

    def test_local_token_uses_internal_url_without_public_configuration(self):
        status, headers, body, api, access_token, client = self.token_request()
        self.assertEqual(status, 200)
        self.assertEqual(body['livekit_url'], 'ws://127.0.0.1:7880')
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'http://localhost:8767')
        self.assertEqual(body['token'], 'synthetic-token-not-a-real-credential')
        api.assert_called_once_with('http://127.0.0.1:7880', 'synthetic-key', 'synthetic-secret')
        access_token.assert_called_once_with('synthetic-key', 'synthetic-secret')
        client.room.create_room.assert_awaited_once()
        client.agent_dispatch.create_dispatch.assert_awaited_once()

    def test_configured_public_url_survives_allowed_remote_origin_and_loopback_proxy_host(self):
        public = 'wss://media.example.invalid'
        for origin, host in [('https://client.example.invalid', 'localhost:8767'),
                             ('https://client.example.invalid', '127.0.0.1:8767'),
                             ('https://client.example.invalid', 'proxy.example.invalid'),
                             ('http://localhost:8767', 'localhost:8767')]:
            with self.subTest(origin=origin, host=host):
                status, headers, body, api, _, _ = self.token_request(public_url=public,
                    origin=origin, host=host, forwarded={'X-Forwarded-Host': 'attacker.example.invalid',
                        'X-Forwarded-Proto': 'http', 'Forwarded': 'host=attacker.example.invalid'})
                self.assertEqual(status, 200)
                self.assertEqual(body['livekit_url'], public)
                self.assertEqual(headers['Access-Control-Allow-Origin'], origin)
                api.assert_called_once_with('http://127.0.0.1:7880', 'synthetic-key', 'synthetic-secret')

    def test_unallowed_or_missing_origin_is_rejected_before_livekit_or_token_creation(self):
        for origin in [None, 'https://unallowed.example.invalid', 'http://client.example.invalid']:
            with self.subTest(origin=origin):
                status, headers, body, api, access_token, _ = self.token_request(
                    public_url='wss://media.example.invalid', origin=origin,
                    forwarded={'X-Forwarded-Host': 'client.example.invalid',
                               'X-Forwarded-Origin': 'https://client.example.invalid'})
                self.assertEqual(status, 403)
                self.assertEqual(body, {'error': 'origin not allowed'})
                self.assertIsNone(headers['Access-Control-Allow-Origin'])
                api.assert_not_called()
                access_token.assert_not_called()

    def test_missing_or_empty_public_url_falls_back_to_configured_url_not_headers(self):
        for public in [None, '']:
            for signaling in ['ws://127.0.0.1:7880', 'wss://configured.example.invalid']:
                with self.subTest(public=public, signaling=signaling):
                    status, _, body, api, _, _ = self.token_request(public_url=public,
                        origin='https://client.example.invalid', host='arbitrary.example.invalid',
                        signaling=signaling, forwarded={'X-Forwarded-Host': 'invented.example.invalid'})
                    self.assertEqual(status, 200)
                    self.assertEqual(body['livekit_url'], signaling)
                    api.assert_called_once_with(signaling.replace('ws://', 'http://', 1)
                        .replace('wss://', 'https://', 1), 'synthetic-key', 'synthetic-secret')

    def test_paid_off_still_refuses_before_livekit_or_token_creation(self):
        status, _, body, api, access_token, _ = self.token_request(paid='0')
        self.assertEqual(status, 503)
        self.assertEqual(body, {'error': 'call service unavailable'})
        api.assert_not_called()
        access_token.assert_not_called()


if __name__ == '__main__':
    unittest.main()
