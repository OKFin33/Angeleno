"""Current Search evidence survives an unread Fetch, never a cancellation."""
import asyncio
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from her_agent.voice_cards import VoiceCards, TOPIC
from her_agent.web_lookup import WebLookup, WebFailure
from test_lookup import CLOCK
from test_web_lookup import FakeWeb, RESULT, URL


class WebCardFailureTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, service, *, session='synthetic', turn=1, supplement=False):
        return await service.execute_async('web_search',
            {'area':'usc_exposition', 'topic':'architecture', 'supplement':supplement},
            session_id=session, user_turn=turn, utterance='PRIVATE_RAW_CHAT_NOT_A_DIAGNOSTIC')

    async def fetch(self, service, result_id, *, session='synthetic', turn=1):
        return await service.execute_async('web_fetch', {'result_id':result_id},
            session_id=session, user_turn=turn, utterance='PRIVATE_RAW_CHAT_NOT_A_DIAGNOSTIC')

    async def test_timeout_preserves_current_batch_and_records_real_selected_id(self):
        api=FakeWeb()
        api.search=AsyncMock(return_value={'results':[RESULT, {**RESULT, 'url':'https://example.net/second', 'title':'Second'}]})
        async def stall(_):await asyncio.Event().wait()
        api.fetch=stall
        service=WebLookup(api,CLOCK);service.begin_turn('synthetic',1)
        _, searched, _=await self.search(service)
        service.remaining_seconds=.02
        selected=searched.citations[1].source_id
        with patch('her_agent.web_lookup.emit') as emit:
            payload,card,intent=await self.fetch(service,selected)
        self.assertEqual(card.status,'unavailable')
        self.assertEqual(card.citations,searched.citations)
        self.assertTrue(all(c.claim_kind=='search_summary' for c in card.citations))
        self.assertIsNone(card.conclusion)
        payload,_=service.reconcile('synthetic','web_fetch',payload,card,intent)
        self.assertEqual(payload['selected_result_id'],selected)
        self.assertEqual(payload['page_read_status'],'unavailable')
        self.assertIn('未读到／未核验',payload['evidence_reading']['spoken_scope'])
        self.assertEqual(payload['evidence_reading']['source_assertions'],[])
        start,end=emit.call_args_list
        self.assertEqual(start.kwargs['result_id'],selected)
        self.assertEqual(start.kwargs['url'],'https://example.net/second')
        expected=httpx.Request('POST','https://api.deepseek.com/v1/tools/fetch',json={'url':start.kwargs['url']})
        self.assertEqual(start.kwargs['request_hash'],hashlib.sha256(expected.content).hexdigest())
        self.assertEqual(end.kwargs['reason'],'web_wait_budget')
        self.assertLessEqual(end.kwargs['remaining_seconds'],start.kwargs['remaining_seconds'])
        self.assertNotIn('PRIVATE_RAW_CHAT',str(emit.call_args_list))

    async def test_success_upgrades_only_actual_selected_page(self):
        api=FakeWeb();api.search=AsyncMock(return_value={'results':[RESULT,{**RESULT,'url':'https://example.net/second'}]})
        service=WebLookup(api,CLOCK);service.begin_turn('synthetic',1)
        _,card,_=await self.search(service)
        payload,card,_=await self.fetch(service,card.citations[1].source_id)
        self.assertEqual(api.requests[-1],('fetch','https://example.net/second'))
        self.assertEqual(card.status,'evidence')
        self.assertEqual([(c.url,c.claim_kind) for c in card.citations],[('https://example.net/second','fetched_page')])
        self.assertIsNone(card.conclusion)
        self.assertNotIn('page_read_status',payload)

    async def test_empty_new_search_does_not_borrow_an_unrelated_card(self):
        api=FakeWeb();service=WebLookup(api,CLOCK);service.begin_turn('synthetic',1)
        await self.search(service)
        service.begin_turn('synthetic',2)
        async def fail(_):raise WebFailure('web_unavailable')
        api.search=fail
        payload,card,_=await self.search(service,turn=2)
        self.assertEqual(card.status,'unavailable');self.assertEqual(card.citations,())
        self.assertIsNone(card.conclusion)
        self.assertNotIn('PRIVATE_RAW_CHAT',str(payload.get('sources')))

    async def test_empty_fetch_body_retains_only_unread_search_evidence(self):
        api=FakeWeb();api.content='  '
        service=WebLookup(api,CLOCK);service.begin_turn('synthetic',1)
        _,searched,_=await self.search(service)
        payload,card,_=await self.fetch(service,searched.citations[0].source_id)
        self.assertEqual(card.status,'unavailable')
        self.assertEqual(card.citations,searched.citations)
        self.assertEqual(payload['detail'],'empty_page')
        self.assertEqual(payload['page_read_status'],'unavailable')
        self.assertIsNone(card.conclusion)

    async def test_explicit_cancel_and_late_adapter_cannot_restore_sources(self):
        api=FakeWeb();service=WebLookup(api,CLOCK);service.begin_turn('synthetic',1)
        _,card,_=await self.search(service)
        ready=asyncio.Event()
        async def late(_):
            ready.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:return {'content':'Late untrusted body'}
        api.fetch=late
        task=asyncio.create_task(self.fetch(service,card.citations[0].source_id))
        await ready.wait();service.request_cancel()
        payload,card,_=await task
        self.assertFalse(payload['accepted']);self.assertEqual(card.status,'cancelled')
        self.assertEqual(card.citations,())
        service.begin_turn('other-identity',1)
        payload,_,_=await self.fetch(service,'rq-2-0',session='other-identity')
        self.assertEqual(payload['status'],'out_of_scope')

    async def test_new_turn_rejects_late_failure_and_preserves_new_budget(self):
        api=FakeWeb();service=WebLookup(api,CLOCK);service.begin_turn('synthetic',1)
        _,card,_=await self.search(service)
        ready,release=asyncio.Event(),asyncio.Event()
        async def late(_):ready.set();await release.wait();raise WebFailure('web_unavailable')
        api.fetch=late
        task=asyncio.create_task(self.fetch(service,card.citations[0].source_id))
        await ready.wait();service.begin_turn('synthetic',2)
        _,new,_=await self.search(service,turn=2)
        remaining=service.remaining_seconds
        release.set();payload,_,_=await task
        self.assertFalse(payload['accepted']);self.assertEqual(service.channel.card('synthetic'),new)
        self.assertEqual(service.remaining_seconds,remaining)

    async def test_supplement_cannot_fetch_previous_version(self):
        service=WebLookup(FakeWeb(),CLOCK);service.begin_turn('synthetic',1)
        _,old,_=await self.search(service)
        await self.search(service,supplement=True)
        payload,_,_=await self.fetch(service,old.citations[0].source_id)
        self.assertEqual(payload['status'],'out_of_scope')

    async def test_failure_packets_remain_ordered_and_addressed(self):
        api=FakeWeb();service=WebLookup(api,CLOCK);service.begin_turn('synthetic',1)
        peer=Mock();peer.publish_data=AsyncMock()
        sender=VoiceCards(service,peer,'synthetic-recipient')
        async def flush(count):
            for _ in range(100):
                if peer.publish_data.await_count>=count:return
                await asyncio.sleep(0)
            self.fail('Missing directed packet')
        try:
            _,card,_=await self.search(service);await flush(2)
            async def fail(_):raise WebFailure('web_unavailable')
            api.fetch=fail
            await self.fetch(service,card.citations[0].source_id);await flush(3)
            packets=[json.loads(c.args[0]) for c in peer.publish_data.await_args_list]
            self.assertEqual([p['sequence'] for p in packets],[1,2,3])
            self.assertEqual([p['card']['phase'] for p in packets],['checking','result','result'])
            self.assertEqual(packets[-1]['card']['status'],'unavailable')
            self.assertEqual(packets[-1]['card']['citations'][0]['claim_kind'],'search_summary')
            for c in peer.publish_data.await_args_list:
                self.assertEqual(c.kwargs,{'reliable':True,'destination_identities':['synthetic-recipient'],'topic':TOPIC})
        finally:await sender.close()
