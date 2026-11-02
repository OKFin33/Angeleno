"""Real DeepSeek SSE, opt-in actor loop and temporary SQLite; offline only."""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from angeleno import provider as llm_factory
from test_provider import OfflineBudget

from angeleno.continuity import Conversation
from angeleno.memory import encoded
from angeleno.memory_tools import TOOLS, prepare_messages, reply, request_chars
from test_memory import StoreFixture, record


def sse(*deltas, finish="stop", done=True):
    lines = ["data: " + json.dumps({"choices": [{"delta": d}]}, ensure_ascii=False) + "\n\n" for d in deltas]
    lines.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}], "usage": {}}) + "\n\n")
    if done:
        lines.append("data: [DONE]\n\n")
    return httpx.Response(200, text="".join(lines))


def call(query="MOCA", *, name="memory_search", args=None, count=1, finish="tool_calls", done=True):
    fragments = []
    for i in range(count):
        arguments = json.dumps({"query": query}, ensure_ascii=False) if args is None else args
        fragments.extend([
            {"tool_calls": [{"index": i, "id": f"call_{i}", "type": "function", "function": {"name": name[:6], "arguments": arguments[:4]}}]},
            {"tool_calls": [{"index": i, "function": {"name": name[6:], "arguments": arguments[4:]}}]},
        ])
    return sse(*fragments, finish=finish, done=done)


class ToolTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.responses = []
        self.provider = llm_factory.DeepSeekChatStream("synthetic-key", max_tokens=512, diagnostics=False, budget=OfflineBudget())
        def handle(request):
            self.requests.append(json.loads(request.content))
            response = self.responses.pop(0)
            if callable(response):
                return response(request)
            return response
        self.provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.conversation = Conversation(self.pack, self.store)

    async def asyncTearDown(self):
        await self.provider._client.aclose()

    def seed_moca(self):
        ids = self.exchange("我想去 MOCA，当代艺术馆。", "可从一幅画开始。")
        self.apply(record("episode.moca", "用户想去 MOCA。", ids, keywords=["MOCA", "当代艺术馆"]))
        return ids

    async def run_reply(self, user="继续聊聊", *, temporary=False):
        messages = self.conversation.temporary([{"role": "user", "content": user}]) if temporary else self.conversation.begin(user)
        seq = None if temporary else self.store.current_turn(self.conversation.session)
        return "".join([p async for p in reply(self.provider, messages, self.store, user_seq=seq)])

    async def test_real_sse_fragments_standard_roundtrip_and_exposure_forgetting(self):
        ids = self.seed_moca()
        self.responses = [call(), sse({"content": "记得，当时说可以从一幅画开始。"})]
        result = await self.run_reply()
        self.conversation.delivered(result)
        self.assertEqual(result, "记得，当时说可以从一幅画开始。")
        self.assertEqual(len(self.requests), 2)
        first, second = self.requests
        self.assertEqual(first["tools"], TOOLS)
        self.assertNotIn("tools", second)
        self.assertEqual(second["tool_choice"], "none")
        self.assertEqual(second["thinking"], {"type": "disabled"})
        assistant, tool = second["messages"][-2:]
        self.assertEqual(assistant["tool_calls"][0]["function"], {"name": "memory_search", "arguments": '{"query": "MOCA"}'})
        self.assertEqual(tool["tool_call_id"], assistant["tool_calls"][0]["id"])
        found = json.loads(tool["content"])
        self.assertEqual(found["records"][0]["id"], "episode.moca")
        self.assertEqual([e["seq"] for e in found["source_readback"]["events"]], ids)
        seq = self.store.current_turn(self.conversation.session)
        exposed = json.loads(self.store.db.execute("SELECT payload FROM turn_inputs WHERE turn=?", (seq,)).fetchone()[0])
        self.assertIn({"id": "episode.moca", "revision": 1}, exposed["records"])
        self.assertEqual(exposed["events"], [*ids, seq])
        self.assertEqual(len(self.store.pending()), 2)  # Protocol is never an exchange.
        self.store.forget("episode.moca")
        self.assertEqual(self.store.pending(), [])
        self.assertNotIn(result, str(list(self.store.db.execute("SELECT text FROM events"))))

    async def test_default_text_path_payload_and_json_maintenance_unchanged(self):
        self.responses = [sse({"content": "普通回复"})]
        self.assertEqual("".join([p async for p in self.provider.chat([{"role": "user", "content": "合成"}])]), "普通回复")
        sent = self.requests[0]
        self.assertNotIn("tools", sent)
        self.assertNotIn("tool_choice", sent)
        self.assertNotIn("response_format", sent)
        self.provider.json_output = True
        self.responses = [sse({"content": '{"retire":[],"updates":[]}'})]
        text = "".join([p async for p in self.provider.chat([{"role": "user", "content": "JSON"}])])
        self.assertEqual(json.loads(text), {"retire": [], "updates": []})
        self.assertEqual(self.requests[-1]["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", self.requests[-1])
        with self.assertRaises(ValueError):
            self.provider.chat_events([], tools=TOOLS)

    async def test_read_only_temporary_does_not_write_events_exposure_or_meta(self):
        self.seed_moca()
        before = self.store.db.total_changes
        self.responses = [call(), sse({"content": "查到了以前聊过的展览。"})]
        await self.run_reply("临时暗号 PRIVATE", temporary=True)
        self.assertEqual(self.store.db.total_changes, before)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM turn_inputs").fetchone()[0], 0)
        self.assertNotIn("PRIVATE", str(list(self.store.db.execute("SELECT text FROM events"))))

    async def test_no_match_is_honest_not_recency_fallback(self):
        self.seed_moca()
        self.responses = [call("完全陌生内容"), sse({"content": "没有查到。"})]
        await self.run_reply()
        found = json.loads(self.requests[-1]["messages"][-1]["content"])
        self.assertEqual(found, {"status": "no_match", "records": [], "record_sources": []})

    async def test_unknown_bad_multiple_calls_fail_without_followup_or_search(self):
        bad = [call(name="web_search"), call(args="{bad"), call(args='{"query":""}'),
               call(args='{"query":1}'), call(args='{"query":"MOCA","limit":10}'),
               call(args='{"query":"MOCA","query":"secret"}'), call(count=2),
               call(args='{}'), call(args='[]'), call(query="x" * 501),
               sse({"tool_calls": [{"index": -1, "id": "bad", "function": {"name": "memory_search", "arguments": '{}'}}]}, finish="tool_calls"),
               sse({"tool_calls": [{"index": 0, "function": {"name": "memory_search", "arguments": '{}'}}]}, finish="tool_calls"),
               sse({"tool_calls": [{"index": 0, "id": "bad", "function": {"name": "memory_search", "arguments": 3}}]}, finish="tool_calls"),
               call(finish="stop"), call(done=False), call(finish="length")]
        for response in bad:
            with self.subTest(response=response):
                self.responses = [response]
                count = len(self.requests)
                with patch.object(self.store, "search", side_effect=AssertionError("Bad protocol must not search")):
                    with self.assertRaises((ValueError, RuntimeError)):
                        await self.run_reply()
                self.assertEqual(len(self.requests), count + 1)

    async def test_second_call_rejected_even_after_spoken_prefix(self):
        self.responses = [call(), sse({"content": "查一下。"}, {"tool_calls": [{"index": 0, "id": "call_again", "function": {"name": "memory_search", "arguments": '{"query":"MOCA"}'}}]}, finish="tool_calls")]
        pieces = []
        messages = self.conversation.begin("再聊聊")
        with self.assertRaises(RuntimeError):
            async for piece in reply(self.provider, messages, self.store, user_seq=self.store.current_turn(self.conversation.session)):
                pieces.append(piece)
        self.assertEqual(pieces, ["查一下。"])
        self.assertEqual(len(self.requests), 2)

    async def test_cancel_before_followup_does_not_send_second_request(self):
        self.responses = [call(), sse({"content": "不该续发"})]
        original = self.store.search
        def cancelling_search(*args, **kwargs):
            value = original(*args, **kwargs)
            asyncio.current_task().cancel()
            return value
        with patch.object(self.store, "search", side_effect=cancelling_search):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_reply()
        self.assertEqual(len(self.requests), 1)

    async def test_close_after_prefix_stops_http_before_tool_or_followup(self):
        closed = asyncio.Event()
        class Paused(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"choices":[{"delta":{"content":"already shown"}}]}\n\n'
                await asyncio.Event().wait()
            async def aclose(self):
                closed.set()
        self.responses = [httpx.Response(200, stream=Paused())]
        messages = self.conversation.begin("合成输入")
        iterator = reply(self.provider, messages, self.store, user_seq=self.store.current_turn(self.conversation.session))
        self.assertEqual(await anext(iterator), "already shown")
        await iterator.aclose()
        self.assertTrue(closed.is_set())
        self.assertEqual(len(self.requests), 1)

    async def test_plain_text_optin_still_one_request(self):
        self.responses = [sse({"content": "只想聊一句。"})]
        self.assertEqual(await self.run_reply(), "只想聊一句。")
        self.assertEqual(len(self.requests), 1)

    async def test_tool_request_budget_counts_definition_and_all_protocol(self):
        messages = self.conversation.begin("纠正 KEEP_CORRECTION")
        messages[0]["content"] += "X" * 63_000
        self.responses = [sse({"content": "不应发送"})]
        with self.assertRaises(ValueError):
            await self.run_large(messages)
        self.assertEqual(self.requests, [])

    async def test_near_full_followup_omits_entire_item_and_keeps_correction(self):
        self.seed_moca()
        messages = self.conversation.begin("纠正 KEEP_CORRECTION；此前周六改为周日。")
        size = request_chars(self.provider, prepare_messages(messages), TOOLS)
        messages[0]["content"] += "X" * (63_900 - size)
        self.responses = [call(), sse({"content": "预算内没能读到记忆。"})]
        await self.run_large(messages)
        self.assertEqual(len(self.requests), 2)
        second = self.requests[-1]
        self.assertLessEqual(len(encoded(second)), 64_000)
        self.assertIn("KEEP_CORRECTION", str(second))
        self.assertIn("周六改为周日", str(second))
        result = json.loads(second["messages"][-1]["content"])
        self.assertEqual(result["status"], "budget_omitted")
        self.assertEqual(result["records"], [])
        turn = self.store.current_turn(self.conversation.session)
        exposed = json.loads(self.store.db.execute("SELECT payload FROM turn_inputs WHERE turn=?", (turn,)).fetchone()[0])
        self.assertNotIn("episode.moca", str(exposed))

    async def run_large(self, messages):
        return [p async for p in reply(self.provider, messages, self.store,
                                      user_seq=self.store.current_turn(self.conversation.session))]

class SearchTests(StoreFixture, unittest.TestCase):
    def test_known_active_weighted_selection_and_hidden_partner_source(self):
        ids = self.exchange("MOCA public source", "SECRET hidden partner")
        self.apply(record("episode.public", "MOCA 公共记录。", [ids[0]], keywords=["MOCA"]),
                   record("episode.hidden", "MOCA 隐藏记录", [ids[1]], known=False),
                   record("card.user", "与查询无关的常驻卡", [ids[0]], kind="user"))
        self.exchange("MOCA 旧记录")
        self.apply(record("episode.retired", "MOCA 应退役", [self.store.pending()[0]["seq"]]))
        self.exchange("MOCA 结束旧事项")
        self.apply(retire=["episode.retired"])
        result = self.store.search("MOCA", budget=4000)
        self.assertEqual([r["id"] for r in result["records"]], ["episode.public"])
        self.assertEqual(result["source_readback"]["events"], [])
        self.assertEqual(result["source_readback"]["records"][0]["omitted"], "unavailable")
        self.assertNotIn("SECRET", encoded(result))
        self.assertNotIn("隐藏记录", encoded(result))

    def test_whole_item_and_source_turn_omission_never_clips_original(self):
        ids = self.exchange("MOCA " + "原话" * 2000, "合成回应")
        self.apply(record("episode.long", "MOCA " + "摘要" * 400, ids, keywords=["MOCA"]))
        small = self.store.search("MOCA", budget=120)
        self.assertEqual(small["status"], "budget_omitted")
        self.assertEqual(small["records"], [])
        large = self.store.search("MOCA", budget=2000)
        self.assertEqual(large["records"][0]["text"], "MOCA " + "摘要" * 400)
        self.assertEqual(large["source_readback"]["events"], [])
        self.assertNotIn("原话", encoded(large))

    def test_merge_exposure_preserves_versions_and_prior_events(self):
        ids = self.exchange("我想去 MOCA")
        self.apply(record("card.user", "用户想去 MOCA", ids, kind="user"))
        chat = Conversation(self.pack, self.store)
        chat.begin("合成无关话题")
        turn = self.store.current_turn(chat.session)
        prior = json.loads(self.store.db.execute("SELECT payload FROM turn_inputs WHERE turn=?", (turn,)).fetchone()[0])
        self.store.extend_input(turn, [{"id": "episode.new", "revision": 4}, {"id": "card.user", "revision": 9}], [ids[0]])
        after = json.loads(self.store.db.execute("SELECT payload FROM turn_inputs WHERE turn=?", (turn,)).fetchone()[0])
        self.assertTrue(all(r in after["records"] for r in prior["records"]))
        self.assertIn({"id": "card.user", "revision": 9}, after["records"])
        self.assertTrue(set(prior["events"]) <= set(after["events"]))
        self.assertEqual(after["events"], sorted(set([*prior["events"], ids[0]])))
