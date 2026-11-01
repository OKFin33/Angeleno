"""Offline product restoration: synthetic files and fake owned processes only."""
from copy import deepcopy
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from her_agent.baseline import configured_pack
from her_agent.budget import Ledger, Prices
from her_agent.runtime import open_store
from scripts import run_trial as trial
from test_voice_runner import configuration
from worker.audio_budget import ASRAllowance
from her_agent.budget import BudgetExceeded
from her_agent.web_lookup import DeepSeekWeb, WebFailure
from test_budget import budget_env, balance_response, mocked_providers
from test_web_lookup import QUERY, URL


def projection():
    return {'LIVEKIT_URL': 'ws://127.0.0.1:7880', 'LIVEKIT_NODE_IP': '100.64.0.2',
            'VOXCPM_URL': 'http://127.0.0.1:18080', 'LIVEKIT_FORCE_TCP': '0',
            'LIVEKIT_PUBLIC_URL': 'wss://synthetic.tail-example.ts.net:9444',
            'ANGELENO_WEB_ALLOWED_ORIGINS': 'https://synthetic.tail-example.ts.net:9443'}


def media():
    return {'port': 7880, 'bind_addresses': ['127.0.0.1'], 'turn': {'enabled': False},
            'rtc': {'tcp_port': 0, 'udp_port': 7882, 'force_tcp': False, 'use_external_ip': False,
                    'port_range_start': 0, 'port_range_end': 0,
                    'stun_servers': ['127.0.0.1:7882'],
                    'ips': {'includes': ['100.64.0.2/32', '127.0.0.1/32']}}}


class TrialConfigurationTests(unittest.TestCase):
    def test_child_entry_uses_real_start_command_and_loopback_health_binding(self):
        from worker import agent
        server = Mock()
        with patch.object(agent, 'AgentServer', server), patch.object(agent, 'main') as start, \
             patch.object(trial.sys, 'argv', ['synthetic-entry', '_worker']):
            trial.worker_entry()
            self.assertEqual(trial.sys.argv, ['synthetic-entry', 'start'])
            agent.AgentServer(port=8081)
            server.assert_called_once_with(host='127.0.0.1', port=8081)
            start.assert_called_once_with()

    def test_default_technical_is_off_product_account_policy_is_explicit_and_process_only(self):
        settings = {'ANGELENO_WEB_REQUEST_LIMIT': '6'}
        before = deepcopy(settings)
        old = {'ANGELENO_WEB_ENABLED': '1', 'ANGELENO_TRIAL_E6_DIR': 'stale'}
        technical = trial.trial_environment(settings, {}, 'technical', inherited=old)
        self.assertEqual(technical['ANGELENO_WEB_ENABLED'], '0')
        self.assertNotIn('ANGELENO_TRIAL_E6_DIR', technical)
        with self.assertRaisesRegex(ValueError, 'test hooks'):
            trial.trial_environment(settings, {}, 'product', inherited=old)
        product = trial.trial_environment(settings, {}, 'product', inherited={})
        self.assertEqual(product['ANGELENO_WEB_ENABLED'], '1')
        self.assertEqual(product['ANGELENO_WEB_REQUEST_LIMIT'], 'account')
        self.assertEqual(product['ANGELENO_WEB_EGRESS'], 'public-fields-v1')
        self.assertEqual(settings, before)
        for source in ({'ANGELENO_PAID_ENABLED': '1'}, {'ANGELENO_WEB_ENABLED': '1'}):
            with self.assertRaises(ValueError):
                trial.trial_environment(source, {}, 'product', inherited={})

    def test_private_media_scope_rejects_wider_listener_wrong_origin_and_old_node_rewrite(self):
        plan = {'proposed_livekit_config_without_keys': media()}
        trial.media_scope(projection(), plan)
        for field, value in [('node_ip', '100.64.0.2'), ('tcp_port', 7881),
                             ('stun_servers', ['stun:example.invalid']), ('use_external_ip', True)]:
            bad = deepcopy(plan); bad['proposed_livekit_config_without_keys']['rtc'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                trial.media_scope(projection(), bad)
        bad = deepcopy(plan); bad['proposed_livekit_config_without_keys']['bind_addresses'] = ['0.0.0.0']
        with self.assertRaises(ValueError):
            trial.media_scope(projection(), bad)
        with self.assertRaises(ValueError):
            trial.media_scope({**projection(), 'ANGELENO_WEB_ALLOWED_ORIGINS': 'https://other.ts.net:9443'}, plan)

    def test_state_preflight_checks_existing_identity_seed_prices_and_ASR_without_writes_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); settings = configuration(root)
            settings['LIVEKIT_API_SECRET'] = 'synthetic-secret-' * 3
            pack = configured_pack(settings, root=root)
            store = open_store(root, pack, 'synthetic', create=True, data_dir=settings['ANGELENO_DATA_DIR']); store.close()
            prices = Prices.from_env(settings, settings['DEEPSEEK_MODEL'])
            ledger_path = root / 'private/state/budget.sqlite3'
            ledger = Ledger(ledger_path, prices, minimum_balance='1', carryover='0', account=settings['DEEPSEEK_API_KEY'])
            for i in range(8):
                ident = ledger.reserve_web('web_fetch', 100000000, ledger.settled_before_check(), 'account')
                ledger.finish_web(ident, charged=None if i == 7 else True, status='cancelled' if i == 7 else 'complete')
            ASRAllowance(root / 'private/state/asr.sqlite3', 5).reserve(1)
            env = trial.trial_environment(settings, projection(), 'product', inherited={})
            files = list((root / 'private').rglob('*.sqlite3'))
            before = {p: p.read_bytes() for p in files}
            with patch('socket.socket', side_effect=AssertionError('No endpoints')), \
                 patch('subprocess.Popen', side_effect=AssertionError('No GPU/services')):
                trial.check_state(env, root=root)
                with self.assertRaisesRegex(ValueError, 'settings differ'):
                    trial.check_state({**env, 'ANGELENO_PRICE_OUTPUT': '9'}, root=root)
                with self.assertRaisesRegex(ValueError, 'Memory identity'):
                    trial.check_state({**env, 'ANGELENO_MEMORY_USER': 'missing'}, root=root)
                with self.assertRaisesRegex(ValueError, 'allowance differs'):
                    trial.check_state({**env, 'ANGELENO_ASR_MAX_SECONDS': '6'}, root=root)
            self.assertEqual(before, {p: p.read_bytes() for p in files})
            self.assertEqual(ledger.summary()['unresolved_reserved_micros'], 5000)


class TrialOwnershipTests(unittest.TestCase):
    def simulate(self, root, *, failure=None, cancel=False, close_failure=False):
        args = SimpleNamespace(stop_at=(datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
                               output=root / 'private/receipt.json', mode='product',
                               tailscale_bin=Path('/synthetic/tailscale'), livekit_bin=Path('/synthetic/livekit'))
        env = trial.trial_environment({}, projection(), 'product', inherited={})
        env.update(LIVEKIT_API_KEY='synthetic-key', LIVEKIT_API_SECRET='synthetic-secret')
        baseline = {'TCP': {'443': {'HTTPS': True}}, 'AllowFunnel': {'old:8443': True}}
        prepared = env, media(), 'synthetic.tail-example.ts.net', ['100.64.0.2:7882', '127.0.0.1:7882'], baseline
        children = [Mock(pid=i) for i in range(1001, 1005)]
        for child in children:
            child.poll.return_value = None
        server = Mock()
        if close_failure:
            server.shutdown.side_effect = RuntimeError('synthetic close failure')
        stop = Mock(); stop.is_set.return_value = cancel; stop.wait.return_value = True
        def stopped(owned):
            for child in owned:
                child.poll.return_value = 0
        def listening(pid, kind):
            return (prepared[3] if kind == 'UDP' else ['127.0.0.1:7880' if pid == 1001 else '127.0.0.1:8081'])
        spawns = [children[0], OSError('synthetic spawn failure')] if failure else children
        with patch.object(trial, 'serve_state', return_value=baseline), \
             patch.object(trial, 'has_route', return_value=True), \
             patch.object(trial, 'listeners', side_effect=listening), \
             patch.object(trial.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')), \
             patch.object(trial.subprocess, 'Popen', side_effect=spawns) as spawn, \
             patch.object(trial, 'stop_children', side_effect=stopped) as cleanup, \
             patch.object(trial, 'HTTPServer', return_value=server), \
             patch.object(trial.threading, 'Thread') as thread, \
             patch.object(trial.threading, 'Event', return_value=stop), \
             patch.object(trial.signal, 'signal'), patch('worker.web_server.handler_for', return_value=object()):
            result = trial.supervise(args, prepared, root=root)
        self.assertTrue(result['temporary_key_config_removed'])
        self.assertTrue(result['owned_children_stopped'])
        self.assertTrue(result['serve_restored_exactly'])
        self.assertFalse(list((root / 'private').rglob('livekit.json')))
        self.assertEqual(result, json.loads(args.output.read_text()))
        if cancel:
            spawn.assert_not_called(); cleanup.assert_not_called(); thread.assert_not_called()
        else:
            self.assertEqual(spawn.call_count, 2 if failure else 4)
            self.assertEqual(cleanup.call_count, 1 if failure else 4)
            server.server_close.assert_called_once()
        return result

    def test_normal_deadline_exit_stops_only_owned_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.simulate(Path(tmp))['status'], 'stopped')

    def test_partial_launch_failure_and_cancel_remove_keys_without_restart(self):
        for kwargs in ({'failure': True}, {'cancel': True}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(self.simulate(Path(tmp), **kwargs)['status'], 'incomplete')

    def test_cleanup_error_does_not_skip_child_stop_key_removal_or_route_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.simulate(Path(tmp), close_failure=True)
            self.assertEqual(result['status'], 'incomplete')
            self.assertEqual(result['cleanup_error_types'], ['RuntimeError'])

    def test_changed_route_baseline_stops_before_any_listener_or_owned_process(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(trial, 'serve_state', return_value={'changed': True}), \
             patch.object(trial.subprocess, 'Popen') as spawn:
            args = SimpleNamespace(stop_at=(datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
                                   output=Path(tmp) / 'private/new.json', tailscale_bin=Path('/synthetic'))
            with self.assertRaisesRegex(RuntimeError, 'baseline changed'):
                trial.supervise(args, ({}, {}, '', [], {}), root=Path(tmp))
            spawn.assert_not_called()
            self.assertFalse(args.output.exists())


class AccountPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.providers = []

    async def asyncTearDown(self):
        await asyncio.gather(*(p.aclose() for p in self.providers))
        self.tmp.cleanup()

    def adapter(self, handler):
        pair = mocked_providers(budget_env(Path(self.tmp.name)), handler)
        self.providers.extend(pair)
        return DeepSeekWeb(pair[0], request_limit='account')

    async def test_current_account_policy_preserves_old_eight_rows_unknown_and_fresh_balance_guard(self):
        requests = []
        def handle(request):
            requests.append(request.method)
            return balance_response() if request.method == 'GET' else httpx.Response(200, json={'results': []})
        api = self.adapter(handle); ledger = api.actor.budget.ledger
        for i in range(8):
            ident = ledger.reserve_web('web_search', 100000000, ledger.settled_before_check(), 8)
            ledger.finish_web(ident, charged=None if i == 7 else True, status='cancelled' if i == 7 else 'complete')
        before = ledger.summary()['rows']
        await api.search(QUERY)
        current = ledger.summary()
        self.assertEqual(current['rows'][:8], before)
        self.assertEqual(len(current['rows']), 9)
        self.assertEqual(current['unresolved_reserved_micros'], 5000)
        self.assertEqual(requests, ['GET', 'POST'])
        with self.assertRaises(BudgetExceeded):
            await DeepSeekWeb(api.actor, request_limit=8).search(QUERY)
        self.assertEqual(requests, ['GET', 'POST', 'GET'])
        for value in (None, True, False, 0, -1, 1.5, {}, [], 'unlimited', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DeepSeekWeb(api.actor, request_limit=value)

    async def test_unknown_fee_and_account_refusal_never_retry_or_reset(self):
        requests = []
        def handle(request):
            requests.append(request.method)
            if len(requests) > 2:
                return httpx.Response(403, text='synthetic account refusal')
            return balance_response() if request.method == 'GET' else httpx.Response(200, text='ambiguous')
        api = self.adapter(handle)
        with self.assertRaises(WebFailure):
            await api.search(QUERY)
        before = api.actor.budget.ledger.summary()['rows']
        with self.assertRaises(BudgetExceeded):
            await api.search(QUERY)
        self.assertEqual(requests, ['GET', 'POST', 'GET'])
        self.assertEqual(api.actor.budget.ledger.summary()['rows'], before)
        self.assertIsNone(before[0]['estimated_micros'])

    async def test_cancel_keeps_unknown_then_a_new_request_can_finish_same_account(self):
        started = asyncio.Event(); release = asyncio.Event(); posts = []
        async def handle(request):
            if request.method == 'GET':
                return balance_response()
            posts.append(request)
            if len(posts) == 1:
                started.set(); await release.wait()
            return httpx.Response(200, json={'results': []})
        api = self.adapter(handle)
        task = asyncio.create_task(api.search(QUERY)); await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await api.search(QUERY)
        rows = api.actor.budget.ledger.summary()['rows']
        self.assertEqual(len(posts), 2)
        self.assertEqual(rows[0]['status'], 'cancelled')
        self.assertIsNone(rows[0]['estimated_micros'])
        self.assertEqual(rows[1]['estimated_micros'], 0)

    async def test_provider_security_refusal_survives_account_mode_reconstruction(self):
        requests = []
        def handle(request):
            requests.append(request.method)
            return balance_response() if request.method == 'GET' else httpx.Response(403)
        api = self.adapter(handle)
        with self.assertRaises(WebFailure):
            await api.fetch(URL)
        with self.assertRaises(WebFailure):
            await DeepSeekWeb(api.actor, request_limit='account').search(QUERY)
        self.assertEqual(requests, ['GET', 'POST'])
