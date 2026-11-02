"""Fail-closed DeepSeek accounting, shared by every paid model request.

Only prices, balances, aggregate usage and dispositions are stored. No prompt,
response text, API key or exception body enters the ledger. Monetary fields are
integer millionths of the explicitly configured currency.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
import os
from pathlib import Path
import sqlite3


APPLICATION_ID = 0x414E4731


class BudgetExceeded(RuntimeError):
    pass


def decimal_setting(value, name, *, positive=False):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{name} must be an explicit finite amount") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be an explicit {'positive' if positive else 'nonnegative'} amount")
    return result


def micros(value):
    return int((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class Prices:
    model: str
    currency: str
    input: Decimal
    cache_hit: Decimal
    output: Decimal
    source: str

    @classmethod
    def from_env(cls, env, model):
        required = ("ANGELENO_PRICE_MODEL", "ANGELENO_BUDGET_CURRENCY", "ANGELENO_PRICE_INPUT",
                    "ANGELENO_PRICE_CACHE_HIT", "ANGELENO_PRICE_OUTPUT", "ANGELENO_PRICE_SOURCE")
        if any(not str(env.get(name, "")).strip() for name in required):
            raise ValueError("Explicit model, currency, input/cache/output prices and price source are required")
        if env["ANGELENO_PRICE_MODEL"] != model:
            raise ValueError("Configured prices do not match DEEPSEEK_MODEL")
        if env["ANGELENO_BUDGET_CURRENCY"] not in {"CNY", "USD"}:
            raise ValueError("ANGELENO_BUDGET_CURRENCY must be CNY or USD")
        return cls(model, env["ANGELENO_BUDGET_CURRENCY"],
                   decimal_setting(env["ANGELENO_PRICE_INPUT"], "input price", positive=True),
                   decimal_setting(env["ANGELENO_PRICE_CACHE_HIT"], "cache price", positive=True),
                   decimal_setting(env["ANGELENO_PRICE_OUTPUT"], "output price", positive=True),
                   env["ANGELENO_PRICE_SOURCE"])

    def cost(self, prompt, completion, hit=0):
        # Rates are currency per million tokens, hence numerically microcurrency
        # per token. Round upward once, retaining small cache-hit charges.
        return int(((prompt - hit) * self.input + hit * self.cache_hit + completion * self.output)
                   .to_integral_value(rounding=ROUND_CEILING))

    def snapshot(self):
        """Operator-supplied rates, never inferred from the request or provider."""
        if (not isinstance(self.model, str) or not self.model.strip()
                or not isinstance(self.currency, str) or self.currency not in {"CNY", "USD"}
                or not isinstance(self.source, str) or not self.source.strip()):
            raise ValueError("Explicit model, currency and price evidence required")
        return {"model": self.model, "currency": self.currency, "source": self.source,
                **{name: str(decimal_setting(getattr(self, name), name, positive=True))
                   for name in ("input", "cache_hit", "output")}}

    @classmethod
    def from_snapshot(cls, snapshot):
        if not isinstance(snapshot, dict) or set(snapshot) != {
                "model", "currency", "source", "input", "cache_hit", "output"}:
            raise ValueError("Invalid request price snapshot")
        prices = cls(**{**snapshot, **{name: decimal_setting(snapshot[name], name, positive=True)
                                      for name in ("input", "cache_hit", "output")}})
        prices.snapshot()
        return prices


class Ledger:
    @staticmethod
    def configuration(prices, *, minimum_balance, carryover, account):
        return {"model": prices.model, "currency": prices.currency,
                    "input": str(prices.input), "cache_hit": str(prices.cache_hit),
                    "output": str(prices.output), "source": prices.source,
                    "minimum": micros(decimal_setting(minimum_balance, "ANGELENO_MIN_BALANCE")),
                    "carryover": micros(decimal_setting(carryover, "ANGELENO_BUDGET_CARRYOVER")),
                    "account": hashlib.sha256(account.encode()).hexdigest()}

    @classmethod
    def validate_existing(cls, path, prices, **settings):
        """Offline preflight: never create/reconcile a ledger or check balance."""
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Existing original budget ledger required")
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("Not an Angeleno budget ledger; left untouched")
            row = db.execute("SELECT payload FROM settings WHERE id=1").fetchone()
            if row is None or json.loads(row[0]) != cls.configuration(prices, **settings):
                raise ValueError("Existing budget settings differ; explicit reconciliation is required")

    def __init__(self, path, prices, *, minimum_balance, carryover, account):
        self.path, self.prices = Path(path), prices
        settings = self.configuration(prices, minimum_balance=minimum_balance,
                                      carryover=carryover, account=account)
        self.minimum, self.carryover = settings["minimum"], settings["carryover"]
        if self.path.is_symlink():
            raise ValueError("Budget ledger must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existing = self.path.exists()
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            if application_id != APPLICATION_ID:
                if existing or application_id != 0 or db.execute("SELECT name FROM sqlite_master").fetchone():
                    raise ValueError("Not an Angeleno budget ledger; left untouched")
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, balance_micros INTEGER)")
                db.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, created TEXT NOT NULL, purpose TEXT NOT NULL, reserved_micros INTEGER NOT NULL, estimated_micros INTEGER, status TEXT NOT NULL, http_status INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, cache_hit_tokens INTEGER)")
                db.execute("INSERT INTO settings VALUES(1,?,NULL)", (json.dumps(settings, sort_keys=True),))
            if json.loads(db.execute("SELECT payload FROM settings WHERE id=1").fetchone()[0]) != settings:
                raise ValueError("Existing budget settings differ; explicit reconciliation is required")
            self._ensure_request_prices(db)
        self.path.chmod(0o600)

    @staticmethod
    def _ensure_request_prices(db):
        # Transactional, additive migration. NULL means the unchanged historical
        # global configuration; never backfill/reprice existing rows.
        if "price_snapshot" not in {row[1] for row in db.execute("PRAGMA table_info(requests)")}:
            db.execute("ALTER TABLE requests ADD COLUMN price_snapshot TEXT")
        db.execute("""CREATE TRIGGER IF NOT EXISTS request_prices_immutable
                      BEFORE UPDATE OF price_snapshot ON requests
                      WHEN NEW.price_snapshot IS NOT OLD.price_snapshot
                      BEGIN SELECT RAISE(ABORT, 'Request prices are immutable'); END""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def settled_before_check(self):
        with self.connect() as db:
            return db.execute("SELECT coalesce(sum(estimated_micros),0) FROM requests").fetchone()[0]

    def request_quote(self, payload, purpose, prices=None):
        prices = self.prices if prices is None else prices
        if not isinstance(prices, Prices):
            raise ValueError("Explicit Prices required")
        snapshot = prices.snapshot()
        prices = Prices.from_snapshot(snapshot)
        if prices.currency != self.prices.currency:
            raise ValueError("Request prices must use the original account currency")
        if purpose not in {"actor", "maintenance"}:
            raise ValueError("Unknown model request purpose")
        if payload.get("model") != prices.model or payload.get("thinking") != {"type": "disabled"}:
            raise ValueError("Request does not match configured non-thinking model prices")
        output = payload.get("max_tokens")
        if type(output) is not int or not 1 <= output <= 2048:
            raise ValueError("Paid output cap must be 1..2048 tokens")
        # UTF-8 bytes bound tokenized text conservatively; reserve extra framing
        # and the full output cap, without assuming cache hits or discounts
        # beyond the explicitly supplied rates.
        input_bound = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 4096
        rate = max(prices.input, prices.cache_hit)
        reserved = int((input_bound * rate + output * prices.output).to_integral_value(rounding=ROUND_CEILING))
        return reserved, json.dumps(snapshot, sort_keys=True)

    def reserve(self, payload, purpose, balance, settled_before, *, prices=None):
        reserved, snapshot = self.request_quote(payload, purpose, prices)
        return self._reserve(reserved, purpose, balance, settled_before, price_snapshot=snapshot)

    def reserve_web(self, purpose, balance, settled_before, request_limit):
        if purpose not in {"web_search", "web_fetch"} or self.prices.currency != "CNY":
            raise ValueError("Web requests require explicit CNY pricing")
        if request_limit != "account" and (type(request_limit) is not int or request_limit < 1):
            raise ValueError("Explicit positive cumulative web request limit or account policy required")
        # Official 2026-10-31 tariff: CNY .005 per nonempty response. Unknown
        # outcomes retain the full reserve; the existing account ledger is used.
        # A current account-use authorization may supersede a historical batch.
        # It does not reset any rows, unknown reserves, floor or fresh balance.
        return self._reserve(5000, purpose, balance, settled_before,
                             None if request_limit == "account" else request_limit)

    def _reserve(self, reserved, purpose, balance, settled_before, web_limit=None, *, price_snapshot=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if web_limit is not None:
                count = db.execute("SELECT count(*) FROM requests WHERE purpose IN ('web_search','web_fetch')").fetchone()[0]
                if count >= web_limit:
                    raise BudgetExceeded("Authorized web request batch exhausted; no request sent")
            # The provider's fresh balance is the source for already billed work.
            # Deduct unknown/cancelled usage plus requests that were not settled
            # before this GET. Known usage is an estimate for audit, never a
            # second lifetime spending cap or a claim about the wallet debit.
            total = db.execute("SELECT coalesce(sum(coalesce(estimated_micros,reserved_micros)),0) FROM requests").fetchone()[0]
            outstanding = total - settled_before
            db.execute("UPDATE settings SET balance_micros=? WHERE id=1", (balance,))
            if balance - outstanding - self.carryover - reserved < self.minimum:
                db.commit()
                raise BudgetExceeded("Insufficient verified balance after reservations; no paid request sent")
            result = db.execute("INSERT INTO requests(created,purpose,reserved_micros,status,price_snapshot) VALUES(?,?,?,?,?)",
                                (datetime.now(timezone.utc).isoformat(), purpose, reserved, "reserved", price_snapshot))
            return result.lastrowid

    def finish_web(self, request_id, *, charged, status, http_status=None):
        # No prompt or URL is stored. Zero tokens indicate request pricing,
        # while estimated_micros NULL preserves uncertain billing after cancel.
        with self.connect() as db:
            row = db.execute("SELECT purpose,reserved_micros FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None or row[0] not in {"web_search", "web_fetch"}:
                raise ValueError("Not a web reservation")
            cost = None if charged is None else row[1] if charged else 0
            db.execute("UPDATE requests SET estimated_micros=?,status=?,http_status=?,prompt_tokens=?,completion_tokens=?,cache_hit_tokens=? WHERE id=?",
                       (cost,status,http_status,*((None,)*3 if charged is None else (0,)*3),request_id))

    def finish(self, request_id, usage, status, http_status=None):
        if not isinstance(status, str) or not status or status == "reserved":
            raise ValueError("Terminal request status required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT purpose,status,price_snapshot,estimated_micros,
                                prompt_tokens,completion_tokens,cache_hit_tokens,http_status
                                FROM requests WHERE id=?""", (request_id,)).fetchone()
            if row is None or row[0] not in {"actor", "maintenance"}:
                raise ValueError("Not a model reservation")
            prices = self.prices if row[2] is None else Prices.from_snapshot(json.loads(row[2]))
            if prices.currency != self.prices.currency:
                raise ValueError("Request snapshot currency differs from account")
            fields = (None,) * 4
            if isinstance(usage, dict):
                prompt, completion, hit = (usage.get(k) for k in
                                           ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens"))
                if all(type(value) is int and value >= 0 for value in (prompt, completion, hit)) and hit <= prompt:
                    fields = (prices.cost(prompt, completion, hit), prompt, completion, hit)
            if row[1] != "reserved":
                if (row[1], *row[3:]) != (status, *fields, http_status):
                    raise ValueError("Request already settled differently; left untouched")
                return
            db.execute("UPDATE requests SET estimated_micros=?,prompt_tokens=?,completion_tokens=?,cache_hit_tokens=?,status=?,http_status=? WHERE id=?",
                       (*fields, status, http_status, request_id))

    def summary(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY id")]
            balance = db.execute("SELECT balance_micros FROM settings WHERE id=1").fetchone()[0]
        return {"currency": self.prices.currency, "minimum_micros": self.minimum,
                "carryover_micros": self.carryover, "last_balance_micros": balance,
                "usage_estimate_micros": sum(row["estimated_micros"] or 0 for row in rows),
                "unresolved_reserved_micros": sum(row["reserved_micros"] for row in rows if row["estimated_micros"] is None),
                "usage_missing": sum(row["prompt_tokens"] is None for row in rows), "rows": rows}


class PaidBudget:
    """Default Flash path is unchanged; an isolated caller may supply Prices.

    request_prices affects only new model reservations, not the ledger's account
    configuration, old rows, Web prices, or previously bound reservations.
    """
    def __init__(self, ledger, api_key, *, request_prices=None):
        self.ledger, self.api_key, self.request_prices = ledger, api_key, request_prices

    async def reserve(self, client, payload, purpose):
        # Reject mismatched experimental prices before even a balance GET.
        self.ledger.request_quote(payload, purpose, self.request_prices)
        balance, settled = await self._balance(client)
        return self.ledger.reserve(payload, purpose, balance, settled, prices=self.request_prices)

    async def reserve_web(self, client, purpose, request_limit):
        balance, settled = await self._balance(client)
        return self.ledger.reserve_web(purpose, balance, settled, request_limit)

    async def _balance(self, client):
        settled = self.ledger.settled_before_check()
        try:
            response = await client.get("https://api.deepseek.com/user/balance",
                                        headers={"Authorization": f"Bearer {self.api_key}"})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("is_available") is not True:
                raise ValueError("Balance unavailable")
            rows = [row for row in data["balance_infos"] if row.get("currency") == self.ledger.prices.currency]
            if len(rows) != 1:
                raise ValueError("Balance currency missing or ambiguous")
            balance = int((decimal_setting(rows[0]["total_balance"], "balance") * 1_000_000)
                          .to_integral_value(rounding=ROUND_FLOOR))
        except Exception:
            # HTTP errors may contain account information. Never emit their body.
            raise BudgetExceeded("Account balance is unknown or unavailable; no paid request sent") from None
        return balance, settled

    def finish(self, *args, **kwargs):
        self.ledger.finish(*args, **kwargs)
