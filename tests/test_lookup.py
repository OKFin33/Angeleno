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
        self.cancel_after_check = False

    def chat_events(self, messages, *, tools=None, temperature=0.7):
        self.sent.append(json.loads(json.dumps(messages, ensure_ascii=False)))
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

    def test_lookup_and_memory_tools_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(
                Path(tmp) / "memory.sqlite3", character=PACK.id, user="lookup-test", seed=seed_digest(PACK),
            )
            try:
                with self.assertRaisesRegex(ValueError, "separate loops"):
                    ConversationRuntime(
                        PACK, self.scripted, store=store, writer=IdleWriter(),
                        memory_tools=True, lookup=self.lookup,
                    )
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
