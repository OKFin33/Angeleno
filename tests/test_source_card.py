"""Source card stays beside the reply; memory stores only a delivered result."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from her_agent.author import load_pack
from her_agent.lookup import TextLookup
from her_agent.memory import MemoryStore, seed_digest
from her_agent.places import FixedClock, load_bundle
from her_agent.provider import TextDelta, ToolCalls
from her_agent.runtime import ConversationRuntime
from test_lookup import FIRST, SECOND, STANDING, IdleWriter, Scripted, call, check_args, dumped


ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / "examples" / "demo" / "author-pack.json")
CLOCK = FixedClock(datetime(2026, 10, 30, 22, 4, 5, tzinfo=timezone.utc))


class SourceCardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = MemoryStore(
            self.root / "memory.sqlite3", character=PACK.id, user="lookup-test", seed=seed_digest(PACK),
        )
        self.lookup = TextLookup(load_bundle(ROOT / "data" / "places"), CLOCK)
        self.scripted = Scripted(self.lookup)
        self.runtime = ConversationRuntime(
            PACK, self.scripted, store=self.store, writer=IdleWriter(), lookup=self.lookup,
        )

    async def asyncTearDown(self):
        if self.runtime is not None:
            await self.runtime.close(flush=False)
        self.tmp.cleanup()

    async def ask(self, user, *rounds, deliver=True):
        self.scripted.rounds.extend(rounds)
        turn = self.runtime.begin(user)
        text = "".join([piece async for piece in self.runtime.stream(turn)])
        if deliver:
            self.runtime.delivered(turn, text, complete=True)
        return turn, text

    async def test_card_is_stored_only_after_delivery(self):
        self.assertFalse(self.store._lookup_table_exists())
        self.assertEqual(self.store._meta("schema"), "2")
        turn, text = await self.ask(
            FIRST,
            [call("place_search", {"query": FIRST}, "c-search")],
            [call("place_check", check_args("周六下午"), "c-check")],
            [TextDelta("我核对了访问页，周六下午可以去。")],
            deliver=False,
        )
        self.assertEqual(text, "我核对了访问页，周六下午可以去。")
        self.assertEqual(turn.source_card["status"], "supported")
        self.assertEqual(turn.source_card["conclusion"], STANDING)
        self.assertEqual(turn.source_card["applicable_date"], "2026-10-31")
        self.assertNotIn(STANDING, text)
        self.assertNotIn("source_status", text)
        self.assertNotIn(turn.source_card["request_id"], text)
        self.assertEqual([row["role"] for row in self.store.pending()], ["user"])
        self.assertEqual(self.store.lookup_results(), [])
        self.runtime.delivered(turn, text, complete=True)
        rows = self.store.lookup_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delivered_text"], text)
        self.assertEqual(rows[0]["conclusion"], STANDING)
        self.assertEqual(rows[0]["applicable_date"], "2026-10-31")
        self.assertEqual(rows[0]["source_id"], "src.fisher.visit")
        self.assertEqual(rows[0]["source_url"], "https://fisher.usc.edu/visit-us/")
        self.assertEqual(rows[0]["status"], "supported")
        self.assertNotIn("form-secret-value", dumped(rows))
        self.assertNotIn("evil.example", dumped(rows))
        self.assertEqual(self.store._meta("schema"), "2")
        self.assertEqual(self.store.revision, 0)

        path = self.store.path
        await self.runtime.close(flush=False)
        self.runtime = None
        reopened = MemoryStore(path, character=PACK.id, user="lookup-test", seed=seed_digest(PACK))
        try:
            self.assertEqual(reopened._meta("schema"), "2")
            self.assertEqual(reopened.lookup_results()[0]["conclusion"], STANDING)
            self.assertEqual(reopened.records(), [])
        finally:
            reopened.close()

    async def test_delivered_result_does_not_enter_the_next_turn(self):
        await self.ask(
            FIRST,
            [call("place_search", {"query": FIRST}, "c-search")],
            [call("place_check", check_args("周六下午"), "c-check")],
            [TextDelta("我核对了访问页，周六下午可以去。")],
        )
        await self.ask(
            SECOND,
            [call("place_check", check_args("下周六", supplement=True), "c-revise")],
            [TextDelta("那就改看下周六。")],
        )
        rows = self.store.lookup_results()
        self.assertEqual([row["applicable_date"] for row in rows], ["2026-10-31", "2026-11-07"])
        self.assertEqual(len({row["request_id"] for row in rows}), 2)
        context, history = self.store.context(self.runtime.conversation.session, SECOND)
        self.assertNotIn(STANDING, dumped(context))
        self.assertNotIn(STANDING, dumped(history))
        self.assertNotIn("2026-10-31", dumped(history))
        follow = self.runtime.begin("今天想聊做饭。")
        self.assertNotIn(STANDING, dumped(follow.messages))
        self.assertNotIn(rows[0]["request_id"], dumped(follow.messages))
        self.assertNotIn(rows[1]["request_id"], dumped(follow.messages))
        self.assertTrue(all(message.get("role") != "tool" for message in follow.messages))
        self.store.forget(None)
        self.assertEqual(self.store.lookup_results(), [])
        self.assertEqual(self.store._meta("schema"), "2")

    async def test_partial_delivery_and_temporary_chat_do_not_record_the_card(self):
        turn, text = await self.ask(
            FIRST,
            [call("place_search", {"query": FIRST}, "c-search")],
            [call("place_check", check_args("周六下午"), "c-check")],
            [TextDelta("我核对了访问页，周六下午可以去。")],
            deliver=False,
        )
        self.runtime.delivered(turn, text[:2], complete=False)
        self.assertEqual(self.store.lookup_results(), [])
        self.assertEqual(self.store.pending()[-1]["delivery"], "interrupted")
        self.assertNotIn(STANDING, self.store.pending()[-1]["text"])

        self.runtime.set_temporary(True)
        temporary, temporary_text = await self.ask(
            FIRST,
            [call("place_search", {"query": FIRST}, "c-search-2")],
            [call("place_check", check_args("周六下午"), "c-check-2")],
            [TextDelta("临时说一下，周六可以去。")],
        )
        self.assertEqual(temporary_text, "临时说一下，周六可以去。")
        self.assertEqual(temporary.source_card["conclusion"], STANDING)
        self.assertEqual(self.store.lookup_results(), [])
        self.assertNotIn("临时说一下", dumped([row["text"] for row in self.store.pending()]))

    def test_fresh_store_forget_does_not_create_lookup_rows(self):
        fresh = MemoryStore(
            self.root / "other.sqlite3", character=PACK.id, user="lookup-other", seed=seed_digest(PACK),
        )
        try:
            self.assertFalse(fresh._lookup_table_exists())
            self.assertEqual(fresh.forget(None), 0)
            self.assertFalse(fresh._lookup_table_exists())
            self.assertEqual(fresh._meta("schema"), "2")
        finally:
            fresh.close()
