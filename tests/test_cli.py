"""Independent text entry and persistent identity under fake HTTP transport."""

import argparse
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

from angeleno import __main__ as cli
from angeleno.config import load_env
from angeleno.runtime import open_store
from test_budget import balance_response, budget_env, mocked_providers
from test_memory import pack_at
from test_provider import DONE, USAGE, event, response


def tool_response():
    fragment = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "search-1", "type": "function",
        "function": {"name": "memory_search", "arguments": '{"query":"MOCA"}'}}]}}]}
    return httpx.Response(200, text="data: " + json.dumps(fragment) + "\n\n" + event(reason="tool_calls", usage=USAGE) + DONE)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_explicit_environment_wins_and_only_selected_file_is_read(self):
        path = self.root / "chosen.env"
        path.write_text('export DEEPSEEK_API_KEY="synthetic-file-key"\nANGELENO_MEMORY_USER=test # comment\nLITERAL="$(never execute)"\n')
        env = load_env({"ANGELENO_ENV_FILE": "chosen.env", "DEEPSEEK_API_KEY": "synthetic-explicit"}, root=self.root)
        self.assertEqual(env["DEEPSEEK_API_KEY"], "synthetic-explicit")
        self.assertEqual(env["ANGELENO_MEMORY_USER"], "test")
        self.assertEqual(env["LITERAL"], "$(never execute)")
        self.assertEqual(load_env({}, root=self.root), {})
        with self.assertRaises(ValueError):
            load_env({"ANGELENO_ENV_FILE": "missing.env"}, root=self.root)

    def test_preview_standard_library_only_with_no_network_or_host_configuration(self):
        env_file = self.root / "empty.env"
        env_file.write_text("")
        pack = pack_at(self.root)
        repository = Path(cli.__file__).resolve().parent.parent
        program = ("import runpy,socket,sys; "
                   "socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); "
                   "sys.argv=['angeleno','preview','--env-file',sys.argv[1],'--pack',sys.argv[2]]; "
                   "runpy.run_module('angeleno',run_name='__main__')")
        result = subprocess.run([sys.executable, "-S", "-B", "-c", program, str(env_file), str(self.root / "pack.json")],
                                cwd=repository, env={"PATH": os.environ.get("PATH", "")},
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["network_calls"], 0)
        self.assertEqual(json.loads(result.stdout)["id"], pack.id)
        self.assertFalse((self.root / "private").exists())

    def test_existing_memory_relocates_without_seed_reset_and_create_is_explicit(self):
        pack = pack_at(self.root)
        with self.assertRaisesRegex(ValueError, "No memory"):
            open_store(self.root, pack, "independent")
        original = open_store(self.root, pack, "independent", create=True)
        original.append("old", "user", "synthetic retained evidence")
        old_path = original.path
        original.close()
        moved = self.root / "private" / "new-state" / pack.id / "independent" / "memory.sqlite3"
        moved.parent.mkdir(parents=True)
        shutil.copy2(old_path, moved)
        restored = open_store(self.root, pack, "independent", data_dir="private/new-state")
        self.assertEqual(restored.pending()[0]["text"], "synthetic retained evidence")
        restored.close()
        before = moved.read_bytes()
        (self.root / "character.md").write_text("Changed seed must not overwrite memory")
        from angeleno.author import load_pack
        with self.assertRaisesRegex(ValueError, "seed differs"):
            open_store(self.root, load_pack(self.root / "pack.json"), "independent", data_dir="private/new-state", create=True)
        self.assertEqual(moved.read_bytes(), before)

    def test_external_state_directory_preserves_identity_and_ledger_guards(self):
        pack = pack_at(self.root)
        from angeleno.provider import configured_providers
        outside = self.root / 'external-state'
        store = open_store(self.root, pack, 'independent', data_dir=outside, create=True)
        store.append('external', 'user', 'synthetic retained evidence')
        store.close()
        store = open_store(self.root, pack, 'independent', data_dir=outside)
        self.assertEqual(store.pending()[0]['text'], 'synthetic retained evidence')
        store.close()
        env = budget_env(self.root, ANGELENO_DATA_DIR=str(outside))
        actor, writer = configured_providers(env, root=self.root)
        asyncio.run(actor.aclose()); asyncio.run(writer.aclose())
        self.assertTrue((outside/'budget.sqlite3').exists())
        with self.assertRaisesRegex(ValueError, 'inside ANGELENO_DATA_DIR'):
            configured_providers({**env, 'ANGELENO_BUDGET_LEDGER':str(self.root/'elsewhere.sqlite3')}, root=self.root)

    def test_default_demo_preview_and_memory_inspection_need_no_paid_settings(self):
        demo = self.root / "examples/demo"
        demo.mkdir(parents=True)
        pack_at(demo)
        (demo / "pack.json").rename(demo / "author-pack.json")
        out = io.StringIO()
        with patch.object(cli, "ROOT", self.root), patch.dict(os.environ, {}, clear=True), redirect_stdout(out):
            cli.main(["preview"])
        self.assertEqual(json.loads(out.getvalue())["id"], "synthetic")
        pack = pack_at(self.root)
        store = open_store(self.root, pack, "inspect", create=True)
        store.append("session", "user", "pending")
        store.close()
        out = io.StringIO()
        with patch.object(cli, "ROOT", self.root), patch.dict(os.environ, {}, clear=True), redirect_stdout(out):
            cli.main(["memory", "--pack", str(self.root / "pack.json"), "--memory-user", "inspect"])
        self.assertEqual(json.loads(out.getvalue())["pending"], 1)

    def test_preview_ignores_configured_memory_identity_without_explicit_selection(self):
        pack_at(self.root)
        env_file = self.root / "preview.env"
        env_file.write_text("ANGELENO_MEMORY_USER=uncreated\nANGELENO_MEMORY_CREATE=1\nANGELENO_MEMORY_TOOLS=1\n")
        out = io.StringIO()
        with patch.object(cli, "ROOT", self.root), patch.dict(os.environ, {}, clear=True), redirect_stdout(out):
            cli.main(["preview", "--env-file", str(env_file), "--pack", str(self.root / "pack.json")])
        self.assertEqual(json.loads(out.getvalue())["network_calls"], 0)
        self.assertFalse((self.root / "private").exists())
        with patch.object(cli, "ROOT", self.root), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "No memory"):
                cli.main(["preview", "--env-file", str(env_file), "--pack", str(self.root / "pack.json"),
                          "--memory-user", "uncreated"])


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = pack_at(self.root)
        self.env = budget_env(self.root)
        self.args = argparse.Namespace(memory_user="independent", memory_create=True, memory_tools=True,
                                       data_dir=self.root / "private" / "state", variant="guided")
        self.requests, self.providers = [], []
        self.actor_calls = 0
        self.actor_response = None
        self.balances = []

    async def asyncTearDown(self):
        await asyncio.gather(*(p.aclose() for p in self.providers))
        self.tmp.cleanup()

    def factory(self, env, **kwargs):
        def handle(request):
            self.requests.append(request)
            if request.method == "GET":
                return self.balances.pop(0) if self.balances else balance_response()
            payload = json.loads(request.content)
            if payload["max_tokens"] == 2048:
                return response('{"retire":[],"updates":[]}')
            self.actor_calls += 1
            if self.actor_response is not None:
                return self.actor_response()
            return tool_response() if self.actor_calls % 2 == 1 else response("A complete synthetic reply.")
        providers = mocked_providers(env, handle)
        self.providers.extend(providers)
        return providers

    async def run_chat(self, inputs):
        out = io.StringIO()
        with patch.object(cli, "ROOT", self.root), \
                patch("angeleno.provider.configured_providers", side_effect=self.factory), \
                patch("builtins.input", side_effect=inputs), redirect_stdout(out):
            await cli.chat(self.args, self.pack, self.env)
        return out.getvalue()

    def inspect(self):
        return open_store(self.root, self.pack, "independent", data_dir=self.root / "private" / "state", purpose="real_user")

    async def test_actual_cli_tool_followup_and_json_maintenance_share_budget(self):
        output = await self.run_chat(["synthetic input", "/exit"])
        self.assertIn("A complete synthetic reply.", output)
        self.assertNotIn("memory_search", output)
        self.assertEqual([r.method for r in self.requests], ["GET", "POST"] * 3)
        posts = [json.loads(r.content) for r in self.requests if r.method == "POST"]
        self.assertEqual([p["max_tokens"] for p in posts], [512, 512, 2048])
        self.assertEqual(posts[-1]["response_format"], {"type": "json_object"})
        batch = json.loads(posts[-1]["messages"][-1]["content"])["batch"]
        self.assertEqual([e["text"] for e in batch["events"]], ["synthetic input", "A complete synthetic reply."])
        ledger = self.providers[0].budget.ledger.summary()
        self.assertEqual([r["purpose"] for r in ledger["rows"]], ["actor", "actor", "maintenance"])
        self.assertEqual(ledger["usage_missing"], 0)
        self.assertTrue(all(p._client.is_closed for p in self.providers))
        store = self.inspect()
        try:
            self.assertEqual(store.pending(), [])
        finally:
            store.close()

    async def test_private_mode_search_never_saves_or_maintains(self):
        await self.run_chat(["/private", "NEVER_SAVE", "/exit"])
        self.assertEqual(self.actor_calls, 2)
        self.assertEqual(len(self.requests), 4)
        store = self.inspect()
        try:
            self.assertEqual(store.pending(), [])
            self.assertEqual(store.db.execute("SELECT count(*) FROM turn_inputs").fetchone()[0], 0)
            self.assertEqual(store.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)
        finally:
            store.close()

    async def test_plain_nonpersistent_chat_makes_one_request_without_memory(self):
        self.args.memory_tools, self.args.memory_user = False, None
        self.actor_response = lambda: response()
        await self.run_chat(["synthetic input", "/exit"])
        self.assertEqual(len(self.requests), 2)
        self.assertFalse((self.root / "private" / "state" / self.pack.id).exists())

    async def test_cancel_keeps_only_displayed_prefix_and_never_starts_maintenance(self):
        closed = asyncio.Event()
        class Cancelled(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield event("shown prefix").encode()
                raise asyncio.CancelledError
            async def aclose(self):
                closed.set()
        self.actor_response = lambda: httpx.Response(200, stream=Cancelled())
        with self.assertRaises(asyncio.CancelledError):
            await self.run_chat(["synthetic input"])
        self.assertTrue(closed.is_set())
        self.assertEqual(len(self.requests), 2)
        store = self.inspect()
        try:
            self.assertEqual([(e["text"], e["delivery"]) for e in store.pending()],
                             [("synthetic input", "complete"), ("shown prefix", "interrupted")])
        finally:
            store.close()
        self.assertEqual(self.providers[0].budget.ledger.summary()["rows"][0]["status"], "cancelled")

    async def test_partial_failure_ends_chat_without_retry_or_maintenance(self):
        self.actor_response = lambda: httpx.Response(200, text=event("partial"))
        output = await self.run_chat(["synthetic input", "must not be sent", "/exit"])
        self.assertIn("没有完整完成", output)
        self.assertEqual(self.actor_calls, 1)
        self.assertEqual(len(self.requests), 2)
        store = self.inspect()
        try:
            self.assertEqual(store.pending()[-1]["delivery"], "interrupted")
        finally:
            store.close()

    async def test_missing_configuration_stops_before_input_or_any_http(self):
        with patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                patch("angeleno.provider.httpx.AsyncClient") as client:
            with self.assertRaisesRegex(ValueError, "disabled"):
                await cli.chat(self.args, self.pack, {})
            client.assert_not_called()
        self.assertFalse((self.root / "private" / "state").exists())

    async def test_tool_followup_checks_new_balance_and_stops_without_second_charge(self):
        self.balances = [balance_response(), balance_response("0")]
        output = await self.run_chat(["synthetic input", "/exit"])
        self.assertIn("没有完整完成", output)
        self.assertEqual([request.method for request in self.requests], ["GET", "POST", "GET"])
        self.assertEqual(len(self.providers[0].budget.ledger.summary()["rows"]), 1)
        store = self.inspect()
        try:
            self.assertEqual([event["role"] for event in store.pending()], ["user"])
        finally:
            store.close()

    async def test_voice_runtime_default_provider_path_also_accounts_actor_and_writer(self):
        from angeleno import voice
        env = {**self.env, "ANGELENO_AUTHOR_PACK": str(self.root / "pack.json"),
               "ANGELENO_MEMORY_USER": "independent", "ANGELENO_MEMORY_PURPOSE": "real_user", "ANGELENO_MEMORY_CREATE": "1",
               "ANGELENO_MEMORY_TOOLS": "1"}
        with patch.object(voice, "configured_providers", side_effect=self.factory):
            runtime = voice.create_voice_runtime(env, root=self.root)
        turn = runtime.begin("synthetic voice transcript")
        text = "".join([piece async for piece in runtime.stream(turn)])
        # Synthetic playout evidence is explicit; generation alone never settles.
        runtime.delivered(turn, text, complete=True)
        await runtime.close()
        self.assertEqual([request.method for request in self.requests], ["GET", "POST"] * 3)
        self.assertEqual([row["purpose"] for row in self.providers[0].budget.ledger.summary()["rows"]],
                         ["actor", "actor", "maintenance"])

    async def test_voice_runtime_missing_prices_sends_no_request(self):
        from angeleno.voice import create_voice_runtime
        env = {"ANGELENO_PAID_ENABLED": "1", "DEEPSEEK_API_KEY": "synthetic-key",
               "ANGELENO_DATA_DIR": str(self.root / "private" / "state"),
               "ANGELENO_AUTHOR_PACK": str(self.root / "pack.json"),
               "ANGELENO_MEMORY_USER": "independent", "ANGELENO_MEMORY_PURPOSE": "real_user", "ANGELENO_MEMORY_CREATE": "1"}
        with patch("angeleno.provider.httpx.AsyncClient") as client:
            with self.assertRaisesRegex(ValueError, "prices"):
                create_voice_runtime(env, root=self.root)
            client.assert_not_called()
        # A failed setup releases its store lock without replacing existing data.
        store = self.inspect()
        store.close()
