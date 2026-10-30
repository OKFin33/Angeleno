"""Offline launcher and local HTTP boundary tests; no real credentials/services."""
import asyncio
from contextlib import redirect_stdout
from http.server import HTTPServer
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import run_voice
from test_memory import pack_at
from worker import web_server


def configuration(root):
    pack_at(root)
    return {'ANGELENO_AUTHOR_PACK': str(root / 'pack.json'),
            'ANGELENO_DATA_DIR': str(root / 'private/state'),
            'ANGELENO_MEMORY_USER': 'synthetic', 'ANGELENO_MEMORY_CREATE': '1',
            'ANGELENO_PAID_ENABLED': '0', 'ANGELENO_ASR_MAX_SECONDS': '5',
            'DEEPSEEK_API_KEY': 'synthetic-key', 'DASHSCOPE_API_KEY': 'synthetic-key',
            'DEEPSEEK_MODEL': 'deepseek-flash',
            'ANGELENO_PRICE_MODEL': 'deepseek-flash', 'ANGELENO_BUDGET_CURRENCY': 'CNY',
            'ANGELENO_PRICE_INPUT': '2', 'ANGELENO_PRICE_CACHE_HIT': '0.04',
            'ANGELENO_PRICE_OUTPUT': '8', 'ANGELENO_PRICE_SOURCE': 'synthetic-pricing-fixture',
            'ANGELENO_MIN_BALANCE': '1', 'ANGELENO_BUDGET_CARRYOVER': '0',
            'LIVEKIT_API_KEY': 'test', 'LIVEKIT_API_SECRET': 'synthetic-secret',
            'LIVEKIT_URL': 'ws://127.0.0.1:7880', 'VOXCPM_URL': 'http://127.0.0.1:18080'}


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
             patch('her_agent.voice.validate_voice_setup'), \
             patch('subprocess.Popen', side_effect=[child, OSError('synthetic spawn failure')]), \
             patch('os.killpg') as kill:
            with self.assertRaisesRegex(OSError, 'synthetic spawn failure'):
                run_voice.run({})
            kill.assert_called_once_with(child.pid, run_voice.signal.SIGTERM)
            self.assertEqual(list((Path(tmp) / 'private').iterdir()), [])

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
             patch('her_agent.voice.validate_voice_setup'), \
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
            self.assertEqual(list((Path(tmp) / 'private').iterdir()), [])

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
                with patch('scripts.run_voice.check_configuration', return_value=[]), \
                     patch.object(web_server, 'create_room_and_token', new_callable=AsyncMock) as create:
                    with urlopen(url + '/healthz', timeout=2) as response:
                        self.assertEqual(json.load(response), {'ready': True, 'scope': 'local-configuration', 'persona_label': '合成示例（林）'})
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


if __name__ == '__main__':
    unittest.main()
