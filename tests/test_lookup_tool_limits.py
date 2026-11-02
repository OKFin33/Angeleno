"""Serial proposal overflow closes within the existing 3-tool/4-model budget."""
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from her_agent.budget import BudgetExceeded
from her_agent.provider import TextDelta, ToolCalls
from her_agent.runtime import ConversationRuntime
from her_agent.web_lookup import DeepSeekWeb, PublicQuery, WebFailure, WebLookup
from test_budget import balance_response, budget_env, mocked_providers
from test_lookup import CLOCK, PACK, Scripted, call
from test_web_lookup import FakeWeb, RESULT, URL


def batch(*calls):
    return ToolCalls(tuple(c.calls[0] for c in calls))


def search(topic, identity):
    return call('web_search', {'area': 'usc_exposition', 'topic': topic}, call_id=identity)


FIRST = batch(search('architecture', 'architecture'), search('visit_hours', 'hours'))
SECOND = batch(call('web_fetch', {'result_id': 'rq-2-0'}, call_id='body'),
               search('small_art_exhibitions', 'extra-search'))


class RecordingLookup(WebLookup):
    def __init__(self, api):
        super().__init__(api, CLOCK)
        self.executions = []

    async def execute_async(self, name, arguments, **kwargs):
        self.executions.append(name)
        return await super().execute_async(name, arguments, **kwargs)


class ToolLimitTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, api, rounds):
        self.actor = Scripted(); self.actor.rounds = rounds
        self.lookup = RecordingLookup(api)
        runtime = ConversationRuntime(PACK, self.actor, lookup=self.lookup)
        try:
            turn = runtime.begin('附近有哪些值得看的建筑，开放信息确定吗？')
            text = ''.join([piece async for piece in runtime.stream(turn)])
            return turn, text
        finally:
            await runtime.close(flush=False)

    def feedback(self, request=-1):
        return {m['tool_call_id']: json.loads(m['content'])
                for m in self.actor.sent[request] if m['role'] == 'tool'}

    async def test_actual_two_searches_then_fetch_and_search_returns_matching_skip(self):
        api = FakeWeb()
        turn, text = await self.run_turn(api, [[FIRST], [SECOND], [TextDelta('输入捕获占位，不判定真实回答。')]])
        self.assertEqual(self.lookup.executions, ['web_search', 'web_search', 'web_fetch'])
        self.assertEqual([row[0] for row in api.requests], ['search', 'search', 'fetch'])
        self.assertEqual(api.requests[-1], ('fetch', URL))
        feedback = self.feedback()
        self.assertEqual(set(feedback), {'architecture', 'hours', 'body', 'extra-search'})
        self.assertEqual(feedback['body']['status'], 'evidence')
        self.assertEqual(feedback['extra-search'], {'accepted': False, 'status': 'not_executed',
                                                   'conclusion': None, 'detail': 'tool_limit_reached'})
        self.assertNotIn('results', feedback['extra-search'])
        self.assertIsNone(self.actor.tool_schemas[-1])
        self.assertEqual(len(self.actor.sent), 3)
        self.assertEqual(turn.source_card['status'], 'evidence')

    async def test_initial_four_call_proposal_executes_original_prefix_only(self):
        topics = ['architecture', 'visit_hours', 'community_spaces', 'small_art_exhibitions']
        calls = batch(*(search(topic, topic) for topic in topics))
        api = FakeWeb()
        await self.run_turn(api, [[calls], [TextDelta('只沿已执行的资料回答。')]])
        self.assertEqual(api.requests, [('search', PublicQuery('usc_exposition', topic).text()) for topic in topics[:3]])
        self.assertEqual(len(self.lookup.executions), 3)
        self.assertEqual(self.feedback()['small_art_exhibitions']['detail'], 'tool_limit_reached')
        self.assertIsNone(self.actor.tool_schemas[-1])

    async def test_real_billing_adapter_never_reserves_or_sends_excess_item(self):
        sent = []
        def handle(request):
            sent.append(request)
            if request.method == 'GET':
                return balance_response()
            if request.url.path.endswith('search'):
                return httpx.Response(200, json={'results': [RESULT]})
            return httpx.Response(200, json={'title': RESULT['title'], 'content': 'Public page.'})
        with tempfile.TemporaryDirectory() as tmp:
            providers = mocked_providers(budget_env(Path(tmp)), handle)
            try:
                await self.run_turn(DeepSeekWeb(providers[0], request_limit=6),
                                    [[FIRST], [SECOND], [TextDelta('只用已有资料。')]])
                summary = providers[0].budget.ledger.summary()
                self.assertEqual([r.method for r in sent], ['GET', 'POST'] * 3)
                self.assertEqual([r['purpose'] for r in summary['rows']], ['web_search', 'web_search', 'web_fetch'])
                self.assertEqual(summary['usage_estimate_micros'], 15000)
                self.assertEqual(summary['unresolved_reserved_micros'], 0)
                self.assertEqual(len(self.actor.sent), 3)
            finally:
                for provider in providers:
                    await provider.aclose()

    async def test_model_ignoring_disabled_tools_never_gets_fifth_request(self):
        first = batch(search('architecture', 'a'), search('visit_hours', 'b'), search('community_spaces', 'c'))
        api = FakeWeb()
        _, text = await self.run_turn(api, [[first], [search('small_art_exhibitions', 'retry-1')],
            [search('architecture', 'retry-2')], [search('visit_hours', 'retry-3')]])
        self.assertEqual(len(self.actor.sent), 4)
        self.assertEqual(len(api.requests), 3)
        self.assertTrue(all(schema is None for schema in self.actor.tool_schemas[1:]))
        self.assertEqual(self.feedback(-1)['retry-2']['detail'], 'tool_limit_reached')
        self.assertIn('The remaining tools did not run', text)

    async def test_cancel_reason_takes_precedence_and_later_requests_cannot_restart(self):
        api = FakeWeb()
        cancel_batch = batch(call('place_cancel', {}, call_id='cancel'), search('small_art_exhibitions', 'after-cancel'))
        await self.run_turn(api, [[FIRST], [cancel_batch], [search('architecture', 'restart')],
                                  [TextDelta('好，停止核对。')]])
        self.assertEqual(self.lookup.executions, ['web_search', 'web_search', 'place_cancel'])
        self.assertEqual(len(api.requests), 2)
        self.assertEqual(self.feedback()['after-cancel']['detail'], 'cancelled')
        self.assertEqual(self.feedback()['restart']['detail'], 'cancelled')
        self.assertTrue(all(s is None for s in self.actor.tool_schemas[2:]))

    async def test_provider_403_stops_remaining_tool_without_dispatch(self):
        api = FakeWeb()
        async def refused(url):
            api.requests.append(('fetch', url))
            raise WebFailure('provider_security_refused')
        api.fetch = refused
        await self.run_turn(api, [[FIRST], [SECOND], [TextDelta('来源核对已停止。')]])
        self.assertEqual(self.lookup.executions, ['web_search', 'web_search', 'web_fetch'])
        self.assertEqual(len(api.requests), 3)
        self.assertEqual(self.feedback()['extra-search']['detail'], 'provider_security_refused')
        self.assertIsNone(self.actor.tool_schemas[-1])

    async def test_balance_failure_never_dispatches_extra_tool_or_model(self):
        api = FakeWeb()
        async def no_balance(url):
            raise BudgetExceeded('synthetic insufficient balance')
        api.fetch = no_balance
        with self.assertRaises(BudgetExceeded):
            await self.run_turn(api, [[FIRST], [SECOND], [TextDelta('must not request')]])
        self.assertEqual(self.lookup.executions, ['web_search', 'web_search', 'web_fetch'])
        self.assertEqual(len(api.requests), 2)
        self.assertEqual(len(self.actor.sent), 2)

    async def test_malformed_excess_item_prevents_the_allowed_prefix_dispatch(self):
        malformed = batch(call('web_fetch', {'result_id': 'rq-2-0'}, call_id='body'),
                          call('web_search', {}, call_id='malformed-extra'))
        api = FakeWeb()
        with self.assertRaises(ValueError):
            await self.run_turn(api, [[FIRST], [malformed]])
        self.assertEqual(self.lookup.executions, ['web_search', 'web_search'])
        self.assertEqual(len(api.requests), 2)


if __name__ == '__main__':
    unittest.main()
