"""Execution controls, not synthetic personality scoring."""
import asyncio
from copy import deepcopy
import itertools
import json
from pathlib import Path
import socket
import tempfile
import unittest

from her_agent.author import load_pack
from her_agent.comparison import (CorpusWeb, GuardedProvider, RequestGuard, RunStopped,
    ScriptedActor, ScriptedWriter, load_config, run_development)
from her_agent.provider import TextDelta
from her_agent.web_lookup import PUBLIC_AREAS, PUBLIC_TOPICS, PUBLIC_PLACES, PublicQuery

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'examples/comparison/development.json'
PACK = load_pack(ROOT/'examples/demo/author-pack.json')


class DevelopmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.c, self.anchors, self.corpus = load_config(CONFIG)

    async def test_three_whole_pairs_share_controls_and_settle_memory(self):
        report = await run_development(self.c, self.anchors, self.corpus, PACK)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(len(report['segments']), 6)
        self.assertEqual(report['model_calls'], 0)
        self.assertIsNone(report['owner_calibration'])
        self.assertFalse(report['ready_for_real_comparison'])
        for anchor in self.anchors:
            arms = [s for s in report['segments'] if s['anchor'] == anchor['id']]
            self.assertEqual(arms[0]['web_calls'], arms[1]['web_calls'])
            for arm in arms:
                self.assertEqual([t['user'] for t in arm['transcript']], anchor['user_turns'])
                self.assertEqual(arm['pending_events'], 0)
                self.assertTrue(all(t['delivery'] == 'complete' for t in arm['transcript']))
                self.assertEqual({t['user_created'] for t in arm['transcript']}, {self.c['clock_utc']})
                calls = [r for r in report['requests'] if r['anchor'] == anchor['id'] and r['role'] == arm['role']]
                self.assertTrue(all(r['status'] == 'completed' for r in calls))
                self.assertEqual(sum(r['kind'] == 'writer' for r in calls), 2)
                first = next(r for r in calls if r['kind'] == 'actor')
                self.assertEqual(set(first['tools']), {'web_search','web_fetch','memory_search','place_search','place_cancel'})
        d2 = next(s for s in report['segments'] if s['anchor'] == 'DEV-D2')
        self.assertEqual([r['card']['applicable_date'] for r in d2['transcript'][:3]],
                         ['2026-10-31','2026-11-07','2026-11-26'])

    async def test_all_public_queries_and_failure_cases_are_same_for_both_arms(self):
        for case in self.corpus['cases']:
            left, right = CorpusWeb(self.corpus, case), CorpusWeb(self.corpus, case)
            for key in itertools.product(PUBLIC_AREAS, PUBLIC_TOPICS, [None,*PUBLIC_PLACES]):
                a = await left.search(PublicQuery(*key))
                self.assertEqual(a, await right.search(PublicQuery(*key)))
                if a['results']:
                    url = a['results'][0]['url']
                    body = await left.fetch(url)
                    self.assertEqual(body, await right.fetch(url))
                    if case == 'truncated_body':
                        self.assertGreater(len(body['content']), 4000)
                    if case == 'conflicting_body':
                        self.assertIn(body['content'], [r['conflicting_body'] for r in self.corpus['records']])
            self.assertEqual(len(left.mapping), 24)
        bad = deepcopy(self.corpus)
        bad['queries'].pop()
        with self.assertRaises(ValueError):
            CorpusWeb(bad, 'normal')
        # Two inputs with identical outbound query words cannot be given
        # different evidence simply because their area IDs differ.
        bad = deepcopy(self.corpus)
        next(r for r in bad['queries'] if r['area']=='jefferson_park' and r['topic']=='visit_hours' and r['place_id']=='place.fisher')['record_ids']=[]
        with self.assertRaises(ValueError):
            CorpusWeb(bad, 'normal')

    async def test_known_branch_inserted_once_unknown_or_dynamic_stops(self):
        def once(a, row, turn):
            return '1' if turn == 1 else None
        report = await run_development(self.c, self.anchors, self.corpus, PACK, branch_selector=once)
        self.assertEqual(report['status'], 'completed')
        for s in report['segments']:
            a = next(a for a in self.anchors if a['id']==s['anchor'])
            self.assertEqual(s['transcript'][1]['user'], a['clarifications'][0]['reply'])
            self.assertEqual(len(s['transcript']), len(a['user_turns'])+1)
        for decision in ('uncovered', '2'):
            report = await run_development(self.c, self.anchors, self.corpus, PACK,
                branch_selector=lambda a, row, turn: decision)
            self.assertEqual(report['status'], 'incomplete')
            d1 = report['segments'][0]
            self.assertEqual(d1['reason'], 'branch_uncovered')
            self.assertEqual(len(d1['transcript']), 1)

    async def test_failed_and_cancelled_attempts_count_before_provider_dispatch(self):
        c = deepcopy(self.c); c['actor_requests_total'] = 1
        guard = RequestGuard(c)
        class Failing(ScriptedActor):
            async def chat_events(self, *args, **kwargs):
                raise ValueError('Synthetic failure')
                yield
        provider = GuardedProvider(Failing(c),guard,'DEV-D1','character','actor',6)
        with self.assertRaises(ValueError):
            _ = [e async for e in provider.chat_events([{'role':'user','content':'x'}])]
        with self.assertRaises(RunStopped):
            _ = [e async for e in provider.chat_events([{'role':'user','content':'x'}])]
        self.assertEqual(len(guard.rows), 1)
        self.assertEqual(guard.rows[0]['status'], 'failed_or_cancelled')
        c['actor_requests_total']=160
        guard = RequestGuard(c)
        ready=asyncio.Event()
        class Hanging(ScriptedActor):
            async def chat_events(self, *args, **kwargs):
                ready.set()
                await asyncio.Future()
                yield
        provider=GuardedProvider(Hanging(c),guard,'DEV-D1','character','actor',6)
        async def consume():
            return [e async for e in provider.chat_events([{'role':'user','content':'x'}])]
        task=asyncio.create_task(consume());await ready.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(guard.rows[0]['error_type'],'CancelledError')

    async def test_request_cap_is_shared_not_reset_by_new_arm(self):
        c=deepcopy(self.c);c['actor_requests_total']=2
        report=await run_development(c,self.anchors,self.corpus,PACK)
        self.assertEqual(report['status'],'incomplete')
        self.assertEqual(len([r for r in report['requests'] if r['kind']=='actor']),2)
        self.assertTrue(all(s['status']=='stopped' for s in report['segments']))

    async def test_cancelled_run_preserves_partial_delivery_and_does_not_start_other_arm(self):
        ready=asyncio.Event()
        class Hanging(ScriptedActor):
            async def chat_events(self,*args,**kwargs):
                yield TextDelta('合成中断前缀')
                ready.set()
                await asyncio.Future()
        task=asyncio.create_task(run_development(self.c,self.anchors,self.corpus,PACK,
            provider_factory=lambda kind,a,role: Hanging(self.c) if kind=='actor' else ScriptedWriter(self.c)))
        await ready.wait();task.cancel();report=await task
        self.assertTrue(report['cancelled'])
        self.assertEqual(report['status'],'incomplete')
        self.assertEqual(len(report['segments']),1)
        row=report['segments'][0]['transcript'][0]
        self.assertEqual((row['output'],row['delivery']),('合成中断前缀','interrupted'))
        self.assertEqual(len(report['requests']),1)
        self.assertEqual(report['requests'][0]['error_type'],'CancelledError')

    async def test_maintenance_failure_stops_segment_without_retry_or_next_turn(self):
        class BadWriter(ScriptedWriter):
            async def chat(self,*args,**kwargs):
                yield 'not JSON'
        def factory(kind,a,role):
            return ScriptedActor(self.c) if kind=='actor' else BadWriter(self.c)
        report=await run_development(self.c,self.anchors,self.corpus,PACK,provider_factory=factory)
        for segment in report['segments']:
            self.assertEqual(segment['status'],'stopped')
            self.assertEqual(len(segment['transcript']),3)
            self.assertEqual(segment['reason'],'maintenance_JSONDecodeError')
        self.assertEqual(sum(r['kind']=='writer' for r in report['requests']),6)

    async def test_scheduled_writer_finishes_before_next_actor(self):
        state={'busy':False,'completed':0}
        class SlowWriter(ScriptedWriter):
            async def chat(self,*args,**kwargs):
                state['busy']=True
                try:
                    await asyncio.sleep(.001)
                    yield '{"retire":[],"updates":[]}'
                    state['completed']+=1
                finally:state['busy']=False
        class ObservingActor(ScriptedActor):
            async def chat_events(self,*args,**kwargs):
                if state['busy']:raise AssertionError('writer crossed next actor boundary')
                async for event in super().chat_events(*args,**kwargs):yield event
        report=await run_development(self.c,self.anchors,self.corpus,PACK,
            provider_factory=lambda kind,a,role: ObservingActor(self.c) if kind=='actor' else SlowWriter(self.c))
        self.assertEqual(report['status'],'completed')
        self.assertEqual(state['completed'],12)

    async def test_offline_fence_stops_accidental_network_before_connect(self):
        class NetworkActor(ScriptedActor):
            async def chat_events(self,*args,**kwargs):
                socket.getaddrinfo('example.org',443)
                yield TextDelta('must not get here')
        report=await run_development(self.c,self.anchors,self.corpus,PACK,
            provider_factory=lambda kind,a,role: NetworkActor(self.c) if kind=='actor' else ScriptedWriter(self.c))
        self.assertEqual(report['status'],'incomplete')
        self.assertTrue(all(s['reason']=='offline_only' for s in report['segments']))
        self.assertTrue(all(not s['web_calls'] for s in report['segments']))

    def test_configuration_rejects_live_switch_and_material_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            for p in CONFIG.parent.glob('*.json'):(path/p.name).write_bytes(p.read_bytes())
            c=deepcopy(self.c);c['paid_execution']=True
            (path/CONFIG.name).write_text(json.dumps(c))
            with self.assertRaises(ValueError):load_config(path/CONFIG.name)
            (path/CONFIG.name).write_text(json.dumps(self.c))
            (path/'anchors.json').write_text('{}')
            with self.assertRaises(ValueError):load_config(path/CONFIG.name)


if __name__=='__main__':unittest.main()
