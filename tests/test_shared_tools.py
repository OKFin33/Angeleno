"""One bounded actor loop can recall this user's memory and check a place."""

from datetime import datetime, timezone
import json
import unittest

from angeleno.lookup import TextLookup
from angeleno.places import FixedClock, load_bundle
from angeleno.provider import TextDelta
from angeleno.runtime import ConversationRuntime
from test_lookup import Scripted, IdleWriter, call, check_args
from test_memory import StoreFixture, record


class RecordingActor(Scripted):
    def __init__(self, service):
        super().__init__(service)
        self.schemas = []

    def chat_events(self, messages, *, tools=None, **kwargs):
        self.schemas.append(tools)
        return super().chat_events(messages, tools=tools, **kwargs)


class SharedToolTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ids = self.exchange("我想去 Fisher 看建筑展。")
        self.apply(record("episode.fisher", "用户想去 Fisher 看建筑展。", ids, keywords=["Fisher"]))
        self.lookup = TextLookup(load_bundle(), FixedClock(datetime(2026, 10, 30, tzinfo=timezone.utc)))
        self.actor = RecordingActor(self.lookup)
        self.runtime = ConversationRuntime(self.pack, self.actor, store=self.store, writer=IdleWriter(),
                                           memory_tools=True, lookup=self.lookup)

    async def asyncTearDown(self):
        await self.runtime.close(flush=False)

    def tearDown(self):
        # The runtime owns and closes the store; only its directory remains.
        self.tmp.cleanup()

    def rounds(self):
        return [
            [call("memory_search", {"query": "Fisher"}, "memory")],
            [call("place_search", {"query": "Fisher"}, "place")],
            [call("place_check", check_args("2026-10-31"), "check")],
            [TextDelta("你上次想看的地方，我核对过这次的资料了。")],
        ]

    async def complete(self, user="上次想去的地方，周六能去吗？"):
        turn = self.runtime.begin(user)
        text = "".join([p async for p in self.runtime.stream(turn)])
        self.runtime.delivered(turn, text, complete=True)
        return turn, text

    async def test_memory_place_search_and_check_share_four_requests_and_delivery(self):
        self.actor.rounds = self.rounds()
        turn, text = await self.complete()
        self.assertEqual(len(self.actor.sent), 4)
        self.assertEqual({t["function"]["name"] for t in self.actor.schemas[0]},
                         {"memory_search", "place_search", "place_check", "place_cancel"})
        self.assertIsNone(self.actor.schemas[-1])
        for schema in self.actor.schemas[1:3]:
            self.assertNotIn("memory_search", [t["function"]["name"] for t in schema])
        found = json.loads(self.actor.sent[1][-1]["content"])
        self.assertEqual(found["records"][0]["id"], "episode.fisher")
        self.assertEqual(turn.source_card["status"], "supported")
        self.assertNotIn("source_status", text)
        self.assertNotIn("memory_search", text)
        rows = self.store.lookup_results()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delivered_text"], text)
        exposure = self.store.db.execute("SELECT payload FROM turn_inputs WHERE turn=?", (turn.user_seq,)).fetchone()[0]
        self.assertIn("episode.fisher", exposure)
        prompt = self.actor.sent[0][0]["content"]
        self.assertIn("可用 memory_search", prompt)
        self.assertIn("可用 place_check", prompt)
        self.assertNotIn("不得声称已查现实资料", prompt)

    async def test_temporary_combined_turn_has_no_new_persistence(self):
        self.actor.rounds = self.rounds()
        before = self.store.db.total_changes
        turn, _ = await self.complete("别保存这次。上次想看的地方周六开放吗？")
        self.assertTrue(turn.temporary)
        self.assertIsNone(turn.user_seq)
        self.assertEqual(self.store.db.total_changes, before)
        self.assertEqual(self.store.lookup_results(), [])
        self.assertEqual(turn.source_card["status"], "supported")

    async def test_repeated_memory_call_is_rejected_without_second_search(self):
        self.actor.rounds = [[call("memory_search", {"query": "Fisher"}, "one")],
                             [call("memory_search", {"query": "Fisher"}, "two")]]
        with self.assertRaisesRegex(ValueError, "One lookup call per class"):
            await self.complete()
        self.assertEqual(len(self.actor.sent), 2)
        self.assertEqual(self.store.lookup_results(), [])

    async def test_fourth_tool_call_cannot_extend_the_request_budget(self):
        self.actor.rounds = self.rounds()[:-1] + [[call("place_cancel", {}, "fourth")]]
        with self.assertRaisesRegex(ValueError, "forbidden"):
            await self.complete()
        self.assertEqual(len(self.actor.sent), 4)
        self.assertIsNone(self.actor.schemas[-1])
        self.assertEqual(self.store.lookup_results(), [])

    async def test_unrequested_memory_tool_cannot_read_a_store(self):
        self.runtime.memory_tools = False
        self.actor.rounds = [[call("memory_search", {"query": "Fisher"})]]
        with self.assertRaisesRegex(ValueError, "Unknown or malformed"):
            await self.complete()
        self.assertEqual(len(self.actor.sent), 1)
        self.assertNotIn("memory_search", [t["function"]["name"] for t in self.actor.schemas[0]])


if __name__ == "__main__":
    unittest.main()
