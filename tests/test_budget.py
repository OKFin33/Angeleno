"""Synthetic balances, usage and failures; never contact a real account."""

import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx

from her_agent.budget import BudgetExceeded
from her_agent.provider import configured_providers, validate_provider_configuration
from test_provider import DONE, USAGE, event, response


def budget_env(root, **changes):
    # Deliberately synthetic prices, not advice or current provider prices.
    return {"ANGELENO_PAID_ENABLED": "1", "DEEPSEEK_API_KEY": "synthetic-account-key",
            "DEEPSEEK_MODEL": "deepseek-flash", "ANGELENO_PRICE_MODEL": "deepseek-flash",
            "ANGELENO_DATA_DIR": str(root / "private" / "state"), "ANGELENO_BUDGET_CURRENCY": "CNY",
            "ANGELENO_PRICE_INPUT": "1", "ANGELENO_PRICE_CACHE_HIT": "0.1",
            "ANGELENO_PRICE_OUTPUT": "2", "ANGELENO_PRICE_SOURCE": "synthetic-test",
            "ANGELENO_MIN_BALANCE": "0", "ANGELENO_BUDGET_CARRYOVER": "0", **changes}


def balance_response(balance="100", currency="CNY"):
    return httpx.Response(200, json={"is_available": True,
                                   "balance_infos": [{"currency": currency, "total_balance": balance}]})


def mocked_providers(env, handler):
    providers = configured_providers(env, root=Path(env["ANGELENO_DATA_DIR"]).parent.parent)
    for provider in providers:
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return providers


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = budget_env(self.root)
        self.providers = []

    async def asyncTearDown(self):
        await asyncio.gather(*(p.aclose() for p in self.providers))
        self.tmp.cleanup()

    def create(self, handler, env=None):
        providers = mocked_providers(self.env if env is None else env, handler)
        self.providers.extend(providers)
        return providers

    async def test_actor_writer_every_request_checks_balance_and_settles_usage(self):
        requests = []
        def handle(request):
            requests.append(request)
            return balance_response() if request.method == "GET" else response()
        actor, writer = self.create(handle)
        for provider in (actor, actor, writer):
            _ = [piece async for piece in provider.chat([{"role": "user", "content": "NEVER_STORE_PROMPT"}])]
        self.assertEqual([r.method for r in requests], ["GET", "POST"] * 3)
        self.assertTrue(all(r.url.path == "/user/balance" for r in requests[::2]))
        last = json.loads(requests[-1].content)
        self.assertEqual(last["response_format"], {"type": "json_object"})
        ledger = actor.budget.ledger
        summary = ledger.summary()
        self.assertEqual(summary["usage_missing"], 0)
        self.assertEqual(summary["usage_estimate_micros"], 3 * 950)
        self.assertEqual([row["purpose"] for row in summary["rows"]], ["actor", "actor", "maintenance"])
        self.assertNotIn(b"NEVER_STORE_PROMPT", ledger.path.read_bytes())
        self.assertNotIn(b"synthetic-account-key", ledger.path.read_bytes())

    async def test_missing_or_invalid_configuration_creates_nothing_and_sends_nothing(self):
        required = ("ANGELENO_PAID_ENABLED", "DEEPSEEK_API_KEY", "ANGELENO_PRICE_MODEL",
                    "ANGELENO_BUDGET_CURRENCY", "ANGELENO_PRICE_INPUT", "ANGELENO_PRICE_CACHE_HIT",
                    "ANGELENO_PRICE_OUTPUT", "ANGELENO_PRICE_SOURCE", "ANGELENO_MIN_BALANCE",
                    "ANGELENO_BUDGET_CARRYOVER")
        cases = [{k: v for k, v in self.env.items() if k != field} for field in required]
        cases += [{**self.env, field: value} for field, value in (
            ("ANGELENO_PAID_ENABLED", "true"), ("ANGELENO_PRICE_MODEL", "other-model"),
            ("ANGELENO_PRICE_INPUT", "NaN"), ("ANGELENO_MIN_BALANCE", "Infinity"),
            ("ANGELENO_PRICE_OUTPUT", "-1"), ("ANGELENO_BUDGET_CARRYOVER", "-1"))]
        with patch("her_agent.provider.httpx.AsyncClient") as client:
            for env in cases:
                with self.assertRaises(ValueError):
                    configured_providers(env, root=self.root)
                with self.assertRaises(ValueError):
                    validate_provider_configuration(env, root=self.root)
            validate_provider_configuration(self.env, root=self.root)
            client.assert_not_called()
        self.assertFalse((self.root / "private" / "state").exists())

    async def test_unknown_unavailable_currency_and_low_balance_never_post(self):
        cases = [httpx.Response(503, text="PRIVATE BALANCE"), httpx.Response(200, json={}),
                 httpx.Response(200, json={"is_available": False}), balance_response(currency="USD"),
                 balance_response("NaN"), balance_response("0")]
        for number, reply in enumerate(cases):
            methods = []
            def handle(request):
                methods.append(request.method)
                return reply
            actor, _ = self.create(handle, budget_env(self.root / str(number)))
            with self.assertRaises(BudgetExceeded):
                _ = [piece async for piece in actor.chat([])]
            self.assertEqual(methods, ["GET"])
            self.assertEqual(actor.budget.ledger.summary()["rows"], [])

    async def test_cancel_failure_and_missing_usage_keep_conservative_reservations(self):
        cases = [httpx.Response(429, text="limited"), httpx.Response(200, text=event("partial")),
                 response(usage=None)]
        for number, reply in enumerate(cases):
            actor, _ = self.create(lambda r: balance_response() if r.method == "GET" else reply,
                                   budget_env(self.root / str(number)))
            try:
                _ = [piece async for piece in actor.chat([])]
            except RuntimeError:
                pass
            row = actor.budget.ledger.summary()["rows"][0]
            self.assertIsNone(row["estimated_micros"])
            self.assertGreater(row["reserved_micros"], 0)
            self.assertIsNone(row["prompt_tokens"])
            self.assertIn(row["status"], {"failed", "completed"})
        class Cancelled(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield event("shown").encode()
                raise asyncio.CancelledError
        actor, _ = self.create(lambda r: balance_response() if r.method == "GET" else httpx.Response(200, stream=Cancelled()),
                               budget_env(self.root / "cancel"))
        with self.assertRaises(asyncio.CancelledError):
            _ = [piece async for piece in actor.chat([])]
        row = actor.budget.ledger.summary()["rows"][0]
        self.assertEqual(row["status"], "cancelled")
        self.assertIsNone(row["estimated_micros"])
        self.assertGreater(row["reserved_micros"], 0)

    async def test_transport_failure_is_recorded_without_error_details(self):
        def handle(request):
            if request.method == "GET":
                return balance_response()
            raise httpx.ConnectError("PRIVATE REQUEST DETAILS")
        actor, _ = self.create(handle)
        with self.assertRaises(httpx.ConnectError):
            _ = [piece async for piece in actor.chat([])]
        row = actor.budget.ledger.summary()["rows"][0]
        self.assertEqual(row["status"], "failed")
        self.assertNotIn(b"PRIVATE REQUEST", actor.budget.ledger.path.read_bytes())

    async def test_restart_retains_unknown_usage_and_fresh_actual_balance_governs(self):
        current_balance = "0.006"
        posts = []
        def handle(request):
            if request.method == "GET":
                return balance_response(current_balance)
            posts.append(request)
            return response(usage=None)
        actor, _ = self.create(handle)
        _ = [piece async for piece in actor.chat([])]
        restarted, _ = self.create(handle)
        with self.assertRaises(BudgetExceeded):
            _ = [piece async for piece in restarted.chat([])]
        self.assertEqual(len(posts), 1)
        self.assertEqual(restarted.budget.ledger.summary()["last_balance_micros"], 6000)
        current_balance = "100"
        _ = [piece async for piece in restarted.chat([])]
        self.assertEqual(len(posts), 2)
        self.assertEqual(restarted.budget.ledger.summary()["usage_missing"], 2)

    async def test_account_floor_and_historical_unknown_carryover_are_enforced(self):
        env = {**self.env, "ANGELENO_MIN_BALANCE": "1", "ANGELENO_BUDGET_CARRYOVER": "0.01"}
        actor, _ = self.create(lambda r: balance_response("1.014"), env)
        with self.assertRaises(BudgetExceeded):
            _ = [piece async for piece in actor.chat([])]
        self.assertEqual(actor.budget.ledger.summary()["carryover_micros"], 10000)
        for field, value in (("ANGELENO_BUDGET_CARRYOVER", "0"), ("ANGELENO_MIN_BALANCE", "0"),
                             ("DEEPSEEK_API_KEY", "different-account"), ("ANGELENO_PRICE_INPUT", "2")):
            with self.assertRaisesRegex(ValueError, "settings differ"):
                configured_providers({**env, field: value}, root=self.root)

    async def test_parallel_stale_balance_reservations_are_atomic(self):
        actor, _ = self.create(lambda r: balance_response())
        ledger = actor.budget.ledger
        payload = {"model": "deepseek-flash", "thinking": {"type": "disabled"}, "max_tokens": 512}
        first = ledger.reserve(payload, "actor", 6000, 0)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve(payload, "maintenance", 6000, 0)
        ledger.finish(first, USAGE, "completed", 200)
        self.assertEqual(len(ledger.summary()["rows"]), 1)

    async def test_foreign_ledger_is_untouched(self):
        path = self.root / "private" / "state" / "budget.sqlite3"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE foreign_data (value TEXT)")
            db.execute("INSERT INTO foreign_data VALUES('preserve')")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "left untouched"):
            configured_providers(self.env, root=self.root)
        self.assertEqual(path.read_bytes(), before)
