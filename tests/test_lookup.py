"""Offline text loop from a registered source to the next turn."""

import contextlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from her_agent.author import load_pack
from her_agent.lookup import TextLookup, main, reply
from her_agent.memory import MemoryStore, seed_digest
from her_agent.places import FixedClock, load_bundle
from her_agent.provider import TextDelta, ToolCalls
from her_agent.runtime import ConversationRuntime


ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "data" / "places"
PACK = load_pack(ROOT / "examples" / "demo" / "author-pack.json")
CLOCK = FixedClock(datetime(2026, 10, 30, 22, 4, 5, tzinfo=timezone.utc))
STANDING = "Open Wednesday through Saturday, 12:00-17:00. Closed Sunday, Monday, and Tuesday."
FIRST = "周六下午能去 Fisher 吗"
SECOND = "我是说下周，不是这周。"
CANCEL = "不用查了。"
TOPIC = "今天想聊做饭。"


def call(name, arguments=None, call_id="c1"):
    return ToolCalls(({
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)},
    },))


def check_args(when, *, supplement=False):
    body = {
        "place_id": "place.fisher",
        "source_id": "src.fisher.visit",
        "question_type": "hours",
        "when": when,
    }
    if supplement:
        body["supplement"] = True
    return body


class Scripted:
    model = "offline-fixture"
    max_tokens = 512

    def __init__(self, service=None):
        self.service = service
        self.rounds = []
        self.sent = []
        self.tool_schemas = []
        self.cancel_after_check = False

    def chat_events(self, messages, *, tools=None, temperature=0.7):
        self.sent.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        self.tool_schemas.append(tools)
        events = self.rounds.pop(0)
        service, cancel = self.service, self.cancel_after_check

        async def iterate():
            for event in events:
                yield event
                if cancel and isinstance(event, ToolCalls) and event.calls[0]["function"]["name"] == "place_check":
                    service.request_cancel()

        return iterate()

    def chat(self, messages, **kwargs):
        raise AssertionError("lookup test must not use the text-only chat entry")

    async def aclose(self):
        return None


class IdleWriter:
    def chat(self, messages, **kwargs):
        raise AssertionError("maintenance is not part of this lookup test")

    async def aclose(self):
        return None


def dumped(value):
    return json.dumps(value, ensure_ascii=False)


class LookupLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bundle = load_bundle(PLACES)
        self.lookup = TextLookup(self.bundle, CLOCK)
        self.scripted = Scripted(self.lookup)
        self.runtime = ConversationRuntime(PACK, self.scripted, lookup=self.lookup)

    async def asyncTearDown(self):
        await self.runtime.close(flush=False)

    async def say(self, user, *rounds):
        self.scripted.rounds.extend(rounds)
        turn = self.runtime.begin(user)
        text = "".join([piece async for piece in self.runtime.stream(turn)])
        self.runtime.delivered(turn, text, complete=True)
        return turn, text

    async def test_query_supplement_cancel_and_topic_change(self):
        first, first_text = await self.say(
            FIRST,
            [call("place_search", {"query": FIRST}, "c-search")],
            [call("place_check", check_args("周六下午"), "c-check")],
            [TextDelta("我核对了访问页，周六下午可以去。")],
        )
        self.assertEqual(first_text, "我核对了访问页，周六下午可以去。")
        self.assertEqual(first.source_card["status"], "supported")
        self.assertEqual(first.source_card["conclusion"], STANDING)
        self.assertEqual(first.source_card["applicable_date"], "2026-10-31")
        self.assertEqual(first.source_card["checked_at"], CLOCK.now().isoformat(timespec="seconds"))
        self.assertEqual(first.source_card["citations"][0]["url"], "https://fisher.usc.edu/visit-us/")
        self.assertEqual(first.source_card["citations"][0]["excerpt"], STANDING)
        self.assertNotIn("source_status", first_text)
        self.assertNotIn(first.source_card["request_id"], first_text)
        self.assertNotIn(STANDING, first_text)
        search_result = json.loads(self.scripted.sent[1][-1]["content"])
        self.assertFalse(search_result["coverage_gap"])
        self.assertFalse(search_result["needs_clarification"])
        self.assertNotIn("exists", search_result)
        self.assertEqual(search_result["hits"][0]["place_id"], "place.fisher")

        second, second_text = await self.say(
            SECOND,
            [call("place_check", check_args("下周六", supplement=True), "c-revise")],
            [TextDelta("那就改看下周六。")],
        )
        self.assertEqual(self.lookup.intent.utterance, FIRST)
        self.assertEqual(self.lookup.intent.question, SECOND)
        self.assertEqual(self.lookup.intent.version, 2)
        self.assertEqual(second.source_card["applicable_date"], "2026-11-07")
        self.assertNotEqual(second.source_card["request_id"], first.source_card["request_id"])
        self.assertEqual(second.source_card["conclusion"], STANDING)
        self.assertNotIn(STANDING, second_text)
        stale = self.lookup.channel.publish(self.lookup.checked_events[0])
        self.assertFalse(stale.accepted)
        self.assertEqual(self.lookup.channel.card(self.lookup.local_session).applicable_date, "2026-11-07")

        third, third_text = await self.say(
            CANCEL,
            [call("place_cancel", {}, "c-cancel")],
            [TextDelta("好，先不查了。")],
        )
        self.assertEqual(third_text, "好，先不查了。")
        self.assertEqual(third.source_card["status"], "cancelled")
        self.assertIsNone(third.source_card["conclusion"])
        self.assertNotIn(STANDING, dumped(third.source_card))
        late = self.lookup.channel.publish(self.lookup.checked_events[1])
        self.assertFalse(late.accepted)
        self.assertIsNone(self.lookup.channel.card(self.lookup.local_session).conclusion)

        fourth, fourth_text = await self.say(TOPIC, [TextDelta("那就聊做饭。")])
        self.assertEqual(fourth_text, "那就聊做饭。")
        self.assertIsNone(fourth.source_card)
        card = self.lookup.channel.card(self.lookup.local_session)
        self.assertEqual(card.status, "cancelled")
        self.assertIsNone(card.conclusion)
        self.assertNotIn("做饭", card.subject)
        self.assertNotIn(STANDING, fourth_text)

        sent = self.scripted.sent
        self.assertEqual(len(sent), 8)
        self.assertIn(STANDING, dumped(sent[2]))
        self.assertIn(STANDING, dumped(sent[4]))
        self.assertNotIn(STANDING, dumped(sent[3]))
        # The independent actor system prompt includes today's LA calendar date.
        # That may equal a fixture date; only conversation/tool history can leak
        # the previous check. Keep the source-text and request-ID checks global.
        self.assertNotIn("2026-10-31", dumped(sent[3][1:]))
        self.assertNotIn("2026-10-31", dumped(sent[4][1:]))
        self.assertIn("2026-11-07", dumped(sent[4]))
        for later in (sent[5], sent[6], sent[7]):
            self.assertNotIn(STANDING, dumped(later))
            self.assertNotIn("2026-11-07", dumped(later[1:]))
        blob = dumped(sent)
        for request_id in (first.source_card["request_id"], second.source_card["request_id"]):
            self.assertNotIn(request_id, blob)
        self.assertNotIn("source_status", blob)
        self.assertNotIn("form-secret-value", blob)
        self.assertNotIn("evil.example", blob)
        self.assertNotIn("Ignore previous instructions", blob)
        self.assertIn("place_search", sent[0][0]["content"])
        self.assertNotIn("不提供持久记忆、搜索或外部操作", sent[0][0]["content"])

    async def test_invalid_search_filter_is_not_a_coverage_claim_or_retry(self):
        # Exact arguments observed in the bounded real-model run. This city
        # string is not one of the catalog's area IDs.
        arguments = {"query": "Natural History Museum of Los Angeles County NHM", "area": "Los Angeles"}
        turn, _ = await self.say(
            "帮我核对今天 NHM 是否开放。",
            [call("place_search", arguments)],
            [TextDelta("这次查询参数没被接受，暂时无法核实。")],
        )
        result = json.loads(self.scripted.sent[1][-1]["content"])
        self.assertEqual(result["status"], "invalid_arguments")
        self.assertIsNone(result["coverage_gap"])
        self.assertEqual(result["invalid_fields"], ["area"])
        self.assertEqual(result["scope"], {"query": arguments["query"], "filters": {"area": "Los Angeles"}})
        self.assertEqual(result["hits"], [])
        self.assertIsNone(turn.source_card)
        self.assertEqual(len(self.scripted.sent), 2)
        self.assertNotIn("place_search", [tool["function"]["name"] for tool in self.scripted.tool_schemas[1]])
        denied, card, intent = self.lookup.execute(
            "place_check", {**check_args("今天"), "place_id": "place.nhm", "source_id": "src.nhm.visit"},
            session_id=self.lookup.local_session, user_turn=1, utterance="NHM",
        )
        self.assertEqual(denied["detail"], "not_in_search")
        self.assertIsNone(card)
        self.assertIsNone(intent)
        self.assertEqual(self.lookup.checked_events, [])

    async def test_actor_gets_catalog_filter_ids_and_unfiltered_nhm_matches(self):
        await self.say("NHM", [call("place_search", {"query": "NHM"})], [TextDelta("找到条目了。")])
        tool = next(t["function"] for t in self.scripted.tool_schemas[0] if t["function"]["name"] == "place_search")
        properties = tool["parameters"]["properties"]
        self.assertEqual(tool["parameters"]["required"], ["query"])
        self.assertEqual(properties["place_id"]["enum"], sorted(self.bundle.catalog.by_id))
        self.assertEqual(properties["area"]["enum"], sorted({e.area for e in self.bundle.catalog.entries}))
        self.assertNotIn("Los Angeles", properties["area"]["enum"])
        result = json.loads(self.scripted.sent[1][-1]["content"])
        self.assertEqual([h["place_id"] for h in result["hits"]], ["place.nhm"])
        self.assertEqual(result["scope"]["filters"], {})
        self.assertFalse(result["coverage_gap"])

    def test_search_scope_and_invalid_id_do_not_broaden_results(self):
        for filters in ({"place_id": "NHM"}, {"place_id": "unknown", "area": "Los Angeles"}):
            with self.subTest(filters=filters):
                result, _, _ = self.lookup._search({"query": "NHM", **filters})
                self.assertEqual(result["status"], "invalid_arguments")
                self.assertIsNone(result["coverage_gap"])
                self.assertEqual(result["hits"], [])
                self.assertEqual(set(result["invalid_fields"]), set(filters))
        for query, filters, gap in (
            ("NHM", {"area": "usc-exposition"}, False),
            ("NHM", {"place_id": "place.nhm"}, False),
            ("NHM", {"area": "jefferson-park"}, True),
            ("未收录的小店", {}, True),
        ):
            with self.subTest(query=query, filters=filters):
                result, _, _ = self.lookup._search({"query": query, **filters})
                self.assertEqual(result["status"], "searched")
                self.assertEqual(result["coverage_gap"], gap)
                self.assertEqual(result["scope"], {"query": query, "filters": filters})
                self.assertEqual([h["place_id"] for h in result["hits"]], [] if gap else ["place.nhm"])

    def test_multiple_place_matches_still_require_clarification(self):
        result, _, _ = self.lookup._search({"query": "Fisher 和 NHM"})
        self.assertFalse(result["coverage_gap"])
        self.assertTrue(result["needs_clarification"])
        self.assertEqual({h["place_id"] for h in result["hits"]}, {"place.fisher", "place.nhm"})

    async def test_check_type_is_declared_and_wrong_type_keeps_the_reason(self):
        from her_agent.live_sources import LiveChecker
        class NoRead:
            async def read(self, source):
                raise AssertionError("Invalid type must not read a source")
        self.lookup.checker = LiveChecker(self.bundle.catalog, self.bundle.registry, CLOCK,
                                          channel=self.lookup.channel, reader=NoRead())
        turn, _ = await self.say(
            "NHM今天开放吗？",
            [call("place_search", {"query": "NHM"})],
            [call("place_check", {"place_id": "place.nhm", "source_id": "src.nhm.visit",
                                  "question_type": "opening_hours", "when": "2026-10-31"})],
            [TextDelta("这次请求类型超出支持范围，无法核实。")],
        )
        search = json.loads(self.scripted.sent[1][-1]["content"])
        self.assertEqual(search["hits"][0]["sources"],
                         [{"source_id": "src.nhm.visit", "question_types": ["hours", "event", "access"]}])
        tool = next(t["function"] for t in self.scripted.tool_schemas[1] if t["function"]["name"] == "place_check")
        properties = tool["parameters"]["properties"]
        self.assertIn("hours", properties["question_type"]["enum"])
        self.assertNotIn("opening_hours", properties["question_type"]["enum"])
        self.assertIn("src.nhm.visit", properties["source_id"]["enum"])
        self.assertIn("place.nhm", properties["place_id"]["enum"])
        result = json.loads(self.scripted.sent[2][-1]["content"])
        self.assertEqual(result["status"], "out_of_scope")
        self.assertEqual(result["detail"], "source_scope")
        self.assertIsNone(result["conclusion"])
        self.assertEqual(turn.source_card["status"], "out_of_scope")
        self.assertEqual(turn.source_card["citations"], [])

    async def test_cancelled_result_never_enters_the_next_turn(self):
        self.scripted.cancel_after_check = True
        self.scripted.rounds.extend([
            [call("place_search", {"query": FIRST}, "c-search")],
            [call("place_check", check_args("周六下午"), "c-check")],
            [TextDelta("那先停住。")],
        ])
        turn = self.runtime.begin(FIRST)
        text = "".join([piece async for piece in self.runtime.stream(turn)])
        self.runtime.delivered(turn, text, complete=True)
        conclusion = self.lookup.last_event.conclusion
        self.assertEqual(conclusion, STANDING)
        self.assertEqual(text, "那先停住。")
        self.assertEqual(turn.source_card["status"], "cancelled")
        self.assertIsNone(turn.source_card["conclusion"])
        self.assertNotIn(conclusion, dumped(self.scripted.sent))
        self.assertNotIn(conclusion, text)
        outcome = self.lookup.channel.publish(self.lookup.last_event)
        self.assertFalse(outcome.accepted)
        self.assertIsNone(self.lookup.channel.card(self.lookup.local_session).conclusion)

        follow = self.runtime.begin(TOPIC)
        self.assertNotIn(conclusion, dumped(follow.messages))
        self.assertNotIn(self.lookup.last_event.request_id, dumped(follow.messages))
        self.scripted.rounds.append([TextDelta("那就聊做饭。")])
        follow_text = "".join([piece async for piece in self.runtime.stream(follow)])
        self.runtime.delivered(follow, follow_text, complete=True)
        self.assertEqual(follow_text, "那就聊做饭。")
        self.assertIsNone(follow.source_card)
        self.assertNotIn(conclusion, dumped(self.scripted.sent[-1]))
        self.assertNotIn(conclusion, dumped(self.runtime.history))

    async def test_previous_tool_result_is_not_replayed(self):
        marker = "STALE-RESULT-MARKER"
        messages = [
            {"role": "system", "content": "系统。"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "old", "type": "function", "function": {"name": "place_check", "arguments": marker},
            }]},
            {"role": "tool", "tool_call_id": "old", "content": marker},
            {"role": "user", "content": TOPIC},
        ]
        self.scripted.rounds.append([TextDelta("那就聊做饭。")])
        text = "".join([piece async for piece in reply(
            self.lookup, self.scripted, messages, session_id="s", user_turn=2, utterance=TOPIC,
        )])
        self.assertEqual(text, "那就聊做饭。")
        self.assertNotIn(marker, dumped(self.scripted.sent))

    def test_lookup_and_memory_tools_can_share_the_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(
                Path(tmp) / "memory.sqlite3", character=PACK.id, user="lookup-test", seed=seed_digest(PACK),
            )
            try:
                runtime = ConversationRuntime(
                    PACK, self.scripted, store=store, writer=IdleWriter(),
                    memory_tools=True, lookup=self.lookup,
                )
                self.assertTrue(runtime.memory_tools)
                self.assertIs(runtime.lookup, self.lookup)
            finally:
                store.close()

    def test_bounded_model_command_stays_disabled(self):
        previous = os.environ.pop("ANGELENO_LOOKUP_LIVE", None)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as first:
                code = main([])
            self.assertEqual(code, 0)
            self.assertIn("disabled", first.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as second:
                code = main(["--live"])
            self.assertEqual(code, 2)
            self.assertIn("disabled", second.getvalue())
        finally:
            if previous is not None:
                os.environ["ANGELENO_LOOKUP_LIVE"] = previous
