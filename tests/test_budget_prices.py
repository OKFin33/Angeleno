"""Mixed-model accounting against synthetic old schemas; no real account access."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

from angeleno.budget import APPLICATION_ID, BudgetExceeded, Ledger, PaidBudget, Prices


FLASH = Prices('deepseek-flash', 'CNY', Decimal('2'), Decimal('.04'), Decimal('8'),
               'synthetic peak reference')
PRO = Prices('deepseek-v4-pro', 'CNY', Decimal('4.5'), Decimal('.15'), Decimal('13.5'),
             'synthetic weekend reference')
SETTINGS = dict(minimum_balance='0.01', carryover='0.002', account='synthetic-account')
USAGE = dict(prompt_tokens=1000, completion_tokens=20, prompt_cache_hit_tokens=100)


def payload(model=FLASH.model):
    return dict(model=model, thinking={'type': 'disabled'}, max_tokens=512,
                messages=[dict(role='user', content='synthetic text')])


def old_database(path):
    """The exact pre-snapshot schema, including known/unknown/in-flight history."""
    with sqlite3.connect(path) as db:
        db.execute(f'PRAGMA application_id={APPLICATION_ID}')
        db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, balance_micros INTEGER)')
        db.execute('CREATE TABLE requests (id INTEGER PRIMARY KEY, created TEXT NOT NULL, purpose TEXT NOT NULL, reserved_micros INTEGER NOT NULL, estimated_micros INTEGER, status TEXT NOT NULL, http_status INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, cache_hit_tokens INTEGER)')
        db.execute('INSERT INTO settings VALUES(1,?,?)',
                   (json.dumps(Ledger.configuration(FLASH, **SETTINGS), sort_keys=True), 1000000))
        db.executemany('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?)', [
            (1, 'synthetic-known', 'actor', 10000, 1964, 'completed', 200, 1000, 20, 100),
            (2, 'synthetic-unknown', 'maintenance', 20000, None, 'cancelled', None, None, None, None),
            (3, 'synthetic-inflight', 'actor', 5000, None, 'reserved', None, None, None, None)])


def historical_rows(path):
    with sqlite3.connect(path) as db:
        return (db.execute('SELECT * FROM settings').fetchall(), db.execute('''SELECT id,created,
                purpose,reserved_micros,estimated_micros,status,http_status,prompt_tokens,
                completion_tokens,cache_hit_tokens FROM requests WHERE id<=3 ORDER BY id''').fetchall())


class RequestPricesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.sqlite3'
        old_database(self.path)

    def open(self):
        return Ledger(self.path, FLASH, **SETTINGS)

    def test_readonly_preflight_keeps_old_bytes_and_does_not_migrate(self):
        before = self.path.read_bytes()
        Ledger.validate_existing(self.path, FLASH, **SETTINGS)
        self.assertEqual(self.path.read_bytes(), before)
        with sqlite3.connect(self.path) as db:
            self.assertNotIn('price_snapshot', [r[1] for r in db.execute('PRAGMA table_info(requests)')])

    def test_additive_migration_preserves_every_old_field_and_unknown(self):
        before = historical_rows(self.path)
        ledger = self.open()
        self.assertEqual(historical_rows(self.path), before)
        with ledger.connect() as db:
            self.assertEqual(db.execute('SELECT price_snapshot FROM requests').fetchall(), [(None,)] * 3)
        self.assertEqual(ledger.summary()['unresolved_reserved_micros'], 25000)
        self.assertEqual(ledger.summary()['usage_estimate_micros'], 1964)
        self.open()
        self.assertEqual(historical_rows(self.path), before)

    def test_failed_migration_rolls_back_schema_and_old_bytes(self):
        before = self.path.read_bytes()
        migrate = Ledger._ensure_request_prices
        def fail(db):
            migrate(db)
            raise RuntimeError('synthetic migration failure')
        with patch.object(Ledger, '_ensure_request_prices', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'migration failure'):
                self.open()
        self.assertEqual(self.path.read_bytes(), before)
        self.open()  # retry is safe after the fully rolled-back attempt

    def test_global_configuration_changes_rejected_before_migration(self):
        before = self.path.read_bytes()
        for prices, settings in [(PRO, SETTINGS), (FLASH, {**SETTINGS, 'account': 'other'}),
                                 (FLASH, {**SETTINGS, 'carryover': '0'}),
                                 (FLASH, {**SETTINGS, 'minimum_balance': '0'})]:
            with self.assertRaisesRegex(ValueError, 'settings differ'):
                Ledger(self.path, prices, **settings)
            self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_pending_settles_at_original_global_prices(self):
        ledger = self.open()
        ledger.finish(3, USAGE, 'completed', 200)
        row = ledger.summary()['rows'][2]
        self.assertEqual(row['estimated_micros'], 1964)
        self.assertIsNone(row['price_snapshot'])
        self.assertEqual(ledger.summary()['rows'][1]['status'], 'cancelled')

    def test_flash_default_and_explicit_pro_snapshot_settle_after_restart(self):
        ledger = self.open()
        ids = [ledger.reserve(payload(prices.model), purpose, 1000000,
                              ledger.settled_before_check(), prices=override)
               for prices, purpose, override in [(FLASH, 'actor', None), (PRO, 'maintenance', PRO)]]
        restarted = self.open()  # original Flash account configuration remains in force
        for ident in ids:
            restarted.finish(ident, USAGE, 'completed', 200)
        rows = restarted.summary()['rows'][-2:]
        self.assertEqual([r['estimated_micros'] for r in rows], [1964, 4335])
        self.assertEqual([json.loads(r['price_snapshot']) for r in rows], [FLASH.snapshot(), PRO.snapshot()])
        self.assertEqual(restarted.prices, FLASH)
        self.assertEqual(restarted.summary()['unresolved_reserved_micros'], 25000)

    def test_snapshot_cannot_be_rebound_even_before_settlement(self):
        ledger = self.open()
        ident = ledger.reserve(payload(PRO.model), 'actor', 1000000, 1964, prices=PRO)
        for snapshot in [None, json.dumps(FLASH.snapshot()), json.dumps(replace(PRO, output=Decimal('1')).snapshot())]:
            with self.subTest(snapshot=snapshot), self.assertRaises(sqlite3.IntegrityError):
                with ledger.connect() as db:
                    db.execute('UPDATE requests SET price_snapshot=? WHERE id=?', (snapshot, ident))
        # A caller changing its selected price object does not affect existing rows.
        ledger.finish(ident, USAGE, 'completed', 200)
        self.assertEqual(ledger.summary()['rows'][-1]['estimated_micros'], 4335)

    def test_known_finish_is_idempotent_and_conflicting_repeat_is_rejected(self):
        ledger = self.open()
        ident = ledger.reserve(payload(PRO.model), 'actor', 1000000, 1964, prices=PRO)
        ledger.finish(ident, USAGE, 'completed', 200)
        before = self.path.read_bytes()
        ledger.finish(ident, dict(USAGE), 'completed', 200)
        self.assertEqual(self.path.read_bytes(), before)
        for usage, status, code in [(None, 'cancelled', None), ({**USAGE, 'completion_tokens': 21}, 'completed', 200),
                                    (USAGE, 'failed', 200), (USAGE, 'completed', 403)]:
            with self.assertRaisesRegex(ValueError, 'already settled differently'):
                ledger.finish(ident, usage, status, code)
            self.assertEqual(self.path.read_bytes(), before)

    def test_cancel_unknown_survives_restart_and_cannot_be_silently_reconciled(self):
        ledger = self.open()
        ident = ledger.reserve(payload(PRO.model), 'maintenance', 1000000, 1964, prices=PRO)
        ledger.finish(ident, None, 'cancelled')
        before = self.path.read_bytes()
        restarted = self.open()
        restarted.finish(ident, None, 'cancelled')
        with self.assertRaisesRegex(ValueError, 'already settled differently'):
            restarted.finish(ident, USAGE, 'completed', 200)
        self.assertEqual(self.path.read_bytes(), before)
        row = restarted.summary()['rows'][-1]
        self.assertIsNone(row['estimated_micros'])
        self.assertGreater(row['reserved_micros'], 0)
        self.assertEqual(json.loads(row['price_snapshot']), PRO.snapshot())

    def test_http_failure_with_usage_and_invalid_usage_remain_distinct(self):
        ledger = self.open()
        for usage, expected in [(USAGE, 4335), (None, None), ({**USAGE, 'prompt_cache_hit_tokens': 1001}, None)]:
            ident = ledger.reserve(payload(PRO.model), 'actor', 1000000, 1964, prices=PRO)
            ledger.finish(ident, usage, 'failed', 403)
            self.assertEqual(ledger.summary()['rows'][-1]['estimated_micros'], expected)

    def test_unknown_request_and_web_id_do_not_settle_as_model(self):
        ledger = self.open()
        web = ledger.reserve_web('web_search', 1000000, 1964, 'account')
        for ident in [9999, web]:
            with self.assertRaisesRegex(ValueError, 'Not a model reservation'):
                ledger.finish(ident, USAGE, 'completed', 200)
        self.assertIsNone(ledger.summary()['rows'][-1]['price_snapshot'])
        ledger.finish_web(web, charged=True, status='completed', http_status=200)
        self.assertEqual(ledger.summary()['rows'][-1]['estimated_micros'], 5000)

    def test_mixed_prices_preserve_balance_floor_carryover_and_unknown_reserves(self):
        ledger = self.open()
        quote, _ = ledger.request_quote(payload(PRO.model), 'actor', PRO)
        # 25k unresolved + 2k carryover + 10k floor are still deducted.
        with self.assertRaises(BudgetExceeded):
            ledger.reserve(payload(PRO.model), 'actor', quote + 36999, 1964, prices=PRO)
        self.assertEqual(len(ledger.summary()['rows']), 3)
        ident = ledger.reserve(payload(PRO.model), 'actor', quote + 37000, 1964, prices=PRO)
        self.assertEqual(ident, 4)

    def test_insert_failure_rolls_back_balance_and_new_row(self):
        ledger = self.open()
        with ledger.connect() as db:
            db.execute("CREATE TRIGGER synthetic_insert_failure BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT, 'test'); END")
        before = self.path.read_bytes()
        with self.assertRaises(sqlite3.IntegrityError):
            ledger.reserve(payload(PRO.model), 'actor', 900000, 1964, prices=PRO)
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_migration_and_mixed_stale_balance_reservations_are_atomic(self):
        barrier = threading.Barrier(2)
        def initialize(_):
            barrier.wait()
            return self.open()
        with ThreadPoolExecutor(2) as pool:
            ledgers = list(pool.map(initialize, range(2)))
        quotes = [l.request_quote(payload(p.model), 'actor', p)[0] for l, p in zip(ledgers, [FLASH, PRO])]
        balance = max(quotes) + 37000
        barrier = threading.Barrier(2)
        def reserve(args):
            ledger, prices = args
            barrier.wait()
            try:
                return ledger.reserve(payload(prices.model), 'actor', balance, 1964, prices=prices)
            except BudgetExceeded:
                return None
        with ThreadPoolExecutor(2) as pool:
            ids = list(pool.map(reserve, zip(ledgers, [FLASH, PRO])))
        self.assertEqual(sum(ident is not None for ident in ids), 1)
        self.assertEqual(len(ledgers[0].summary()['rows']), 4)

    def test_concurrent_identical_settlement_is_once_and_conflicts_cannot_overwrite(self):
        ledger = self.open()
        ident = ledger.reserve(payload(PRO.model), 'actor', 1000000, 1964, prices=PRO)
        barrier = threading.Barrier(2)
        def finish(_):
            barrier.wait()
            self.open().finish(ident, USAGE, 'completed', 200)
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(finish, range(2)))
        self.assertEqual(ledger.summary()['rows'][-1]['estimated_micros'], 4335)
        with self.assertRaisesRegex(ValueError, 'already settled differently'):
            self.open().finish(ident, {**USAGE, 'completion_tokens': 0}, 'completed', 200)

    def test_concurrent_conflicting_finishes_choose_one_terminal_result(self):
        ledger = self.open()
        ident = ledger.reserve(payload(PRO.model), 'actor', 1000000, 1964, prices=PRO)
        barrier = threading.Barrier(2)
        def finish(completion):
            barrier.wait()
            try:
                self.open().finish(ident, {**USAGE, 'completion_tokens': completion}, 'completed', 200)
                return completion
            except ValueError as exc:
                self.assertIn('already settled differently', str(exc))
                return None
        with ThreadPoolExecutor(2) as pool:
            winners = [value for value in pool.map(finish, [20, 21]) if value is not None]
        self.assertEqual(len(winners), 1)
        row = ledger.summary()['rows'][-1]
        self.assertEqual(row['completion_tokens'], winners[0])
        self.assertEqual(row['estimated_micros'], PRO.cost(1000, winners[0], 100))

    def test_malformed_stored_snapshot_fails_closed_without_settlement(self):
        ledger = self.open()
        # Deliberately damaged synthetic database; never repair it by repricing.
        with ledger.connect() as db:
            ident = db.execute("""INSERT INTO requests(created,purpose,reserved_micros,status,price_snapshot)
                                  VALUES('synthetic','actor',10000,'reserved',?)""",
                               (json.dumps({**PRO.snapshot(), 'currency': 'USD'}),)).lastrowid
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'currency differs'):
            ledger.finish(ident, USAGE, 'completed', 200)
        self.assertEqual(self.path.read_bytes(), before)


class PaidRequestPricesTests(unittest.IsolatedAsyncioTestCase):
    async def test_pro_provider_uses_one_original_ledger_and_real_usage_parser_with_fake_HTTP(self):
        from angeleno.provider import DeepSeekChatStream
        from test_provider import response
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / 'synthetic.sqlite3', FLASH, **SETTINGS)
            budget = PaidBudget(ledger, 'synthetic-account', request_prices=PRO)
            provider = DeepSeekChatStream('synthetic-account', model=PRO.model, budget=budget,
                                          json_output=True, purpose='maintenance')
            calls = []
            def handle(request):
                calls.append(request)
                if request.method == 'GET':
                    return httpx.Response(200, json={'is_available': True, 'balance_infos': [
                        {'currency': 'CNY', 'total_balance': '1'}]})
                return response('{"synthetic":true}')
            provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            try:
                self.assertEqual([p async for p in provider.chat([], temperature=.2)], ['{"synthetic":true}'])
            finally:
                await provider.aclose()
            self.assertEqual([r.method for r in calls], ['GET', 'POST'])
            body = json.loads(calls[-1].content)
            self.assertEqual(body['model'], PRO.model)
            self.assertEqual(body['thinking'], {'type': 'disabled'})
            self.assertNotIn('price_snapshot', body)
            self.assertEqual(ledger.summary()['rows'][0]['estimated_micros'], 4335)
            self.assertEqual(ledger.prices, FLASH)

    async def test_explicit_experiment_uses_same_ledger_and_mismatches_never_get_balance(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / 'synthetic.sqlite3', FLASH, **SETTINGS)
            budget = PaidBudget(ledger, 'synthetic-account', request_prices=PRO)
            methods = []
            def handle(request):
                methods.append(request.method)
                return httpx.Response(200, json={'is_available': True, 'balance_infos': [
                    {'currency': 'CNY', 'total_balance': '1'}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                for prices, body in [(PRO, payload()), (replace(PRO, currency='USD'), payload(PRO.model)),
                                     (replace(PRO, source=' '), payload(PRO.model)),
                                     (replace(PRO, input=Decimal('NaN')), payload(PRO.model)),
                                     (PRO, {**payload(PRO.model), 'thinking': {'type': 'enabled'}}),
                                     (PRO, {**payload(PRO.model), 'max_tokens': 2049})]:
                    with self.subTest(prices=prices), self.assertRaises(ValueError):
                        await PaidBudget(ledger, 'synthetic-account', request_prices=prices).reserve(client, body, 'actor')
                self.assertEqual(methods, [])
                ident = await budget.reserve(client, payload(PRO.model), 'actor')
            budget.finish(ident, USAGE, 'completed', 200)
            self.assertEqual(methods, ['GET'])
            row = ledger.summary()['rows'][0]
            self.assertEqual(row['estimated_micros'], 4335)
            self.assertEqual(json.loads(row['price_snapshot']), PRO.snapshot())
            self.assertEqual(ledger.prices, FLASH)
