"""Lossless voice views against production adapters with synthetic public data."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import AsyncMock, Mock

from her_agent.lookup import voice_request_messages, voice_tool_payload
from her_agent.memory import encoded
from her_agent.places.model import Citation
from her_agent.voice_cards import VoiceCards
from her_agent.web_lookup import WebLookup
from test_lookup import CLOCK
from test_voice_presentation import PublicReplay, expand_evidence


class VoiceEvidenceViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api=PublicReplay(fail=True)
        self.service=WebLookup(self.api,CLOCK)
        self.session='synthetic-evidence-turn'
        self.service.begin_turn(self.session,1)
        participant=Mock();participant.publish_data=AsyncMock()
        self.sender=VoiceCards(self.service,participant,'synthetic-recipient')
        self.messages=[{'role':'system','content':'Synthetic source evidence test'},
                       {'role':'user','content':'公开查询，只改变资料呈现，不是实际模型回答。'}]
        self.body=None

    async def asyncTearDown(self):
        await self.sender.close()

    async def search(self):
        body,card,intent=await self.service.execute_async('web_search',
            {'area':'usc_exposition','topic':'visit_hours','place_id':'place.fisher'},
            session_id=self.session,user_turn=1,utterance=self.messages[1]['content'])
        body,card=self.service.reconcile(self.session,'web_search',body,card,intent)
        self.messages.append({'role':'tool','tool_call_id':'search-call',
            'content':encoded(voice_tool_payload(self.service,body,card,self.session))})
        return body,card

    async def fetch(self):
        body,card,intent=await self.service.execute_async('web_fetch',
            {'result_id':next(iter(self.service.results))},session_id=self.session,user_turn=1,
            utterance=self.messages[1]['content'])
        body,card=self.service.reconcile(self.session,'web_fetch',body,card,intent)
        self.messages.append({'role':'tool','tool_call_id':'fetch-call',
            'content':encoded(voice_tool_payload(self.service,body,card,self.session))})
        self.body=body
        return body,card

    def project(self,messages=None):
        return voice_request_messages(self.service,self.messages if messages is None else messages,self.session)

    def assert_roundtrip(self,actual,canonical):
        for old,new in zip(canonical,actual,strict=True):
            if old['role']=='tool':self.assertEqual(expand_evidence(actual,new),json.loads(old['content']))
            else:self.assertEqual(old,new)

    async def test_three_copies_become_one_without_changing_card_registry_or_canonical_history(self):
        await self.search();await self.fetch()
        history=deepcopy(self.messages)
        card=self.service.channel.card(self.session)
        registry=deepcopy(self.service.results)
        view=self.project()
        self.assert_roundtrip(view,history)
        self.assertEqual(self.messages,history)
        self.assertEqual(self.service.results,registry)
        self.assertEqual(self.service.channel.card(self.session),card)
        first=json.loads(view[2]['content']);last=json.loads(view[3]['content'])
        self.assertEqual(last['page_read_status'],'unavailable')
        self.assertEqual(last['evidence_reading']['source_assertions'],[])
        self.assertEqual(first['response_presentation']['card_delivery'],'not_confirmed')
        for source in history[2:]:
            for citation in json.loads(source['content'])['sources']:
                copies=[s for m in view[2:] for s in json.loads(m['content']).get('sources',[])
                        if s.get('source_id')==citation['source_id'] and s.get('excerpt')==citation['excerpt']]
                self.assertEqual(len(copies),1)
        self.assertTrue(all('url' not in r and 'summary' not in r for r in first['results']))
        self.assertTrue(all('evidence_ref' in r and 'url' not in r and 'excerpt' not in r for r in last['sources']))

    async def test_cropped_canonical_context_rebuild_is_self_contained(self):
        await self.search();await self.fetch()
        cropped=[*self.messages[:2],self.messages[-1]]
        view=self.project(cropped)
        self.assertEqual(json.loads(view[-1]['content'])['sources'],self.body['sources'])
        self.assert_roundtrip(view,cropped)

    async def test_already_projected_cropped_request_cannot_send_a_dangling_reference(self):
        await self.search();await self.fetch()
        view=self.project()
        with self.assertRaisesRegex(ValueError,'no visible complete source'):
            self.project([*view[:2],view[-1]])

    async def test_partial_quote_cannot_back_the_original_hash(self):
        await self.search();await self.fetch()
        cropped=deepcopy(self.messages)
        previous=json.loads(cropped[2]['content'])
        previous['sources'][0]['excerpt']=previous['sources'][0]['excerpt'][:12]
        # Remove the duplicate result row as a request fitter might do.
        previous['results']=[]
        cropped[2]['content']=encoded(previous)
        view=self.project(cropped)
        self.assertEqual(json.loads(view[-1]['content'])['sources'][0],self.body['sources'][0])
        self.assert_roundtrip(view,cropped)

    async def test_same_id_with_changed_content_or_url_is_not_a_reference(self):
        await self.search();await self.fetch()
        for field,value in [('excerpt','Different hours: Monday10-11, except holidays.'),
                            ('url','https://example.org/a-different-public-source')]:
            with self.subTest(field=field):
                raw=deepcopy(self.messages);body=json.loads(raw[-1]['content'])
                body['sources'][0][field]=value
                card=self.service.channel.card(self.session)
                old_card=card
                changed=Citation(**body['sources'][0])
                self.service.channel._cards[self.session]=replace(card,citations=(changed,*card.citations[1:]))
                try:
                    raw[-1]['content']=encoded(body)
                    view=self.project(raw)
                    self.assertEqual(json.loads(view[-1]['content'])['sources'][0],body['sources'][0])
                    self.assert_roundtrip(view,raw)
                finally:self.service.channel._cards[self.session]=old_card

    async def test_equal_ids_but_different_search_summary_preserve_both_texts(self):
        await self.search()
        raw=deepcopy(self.messages);body=json.loads(raw[-1]['content'])
        body['results'][0]['summary']='Different provider text, same ID. Closed only university holidays.'
        raw[-1]['content']=encoded(body)
        view=self.project(raw);projected=json.loads(view[-1]['content'])
        self.assertEqual(projected['results'][0],body['results'][0])
        self.assertEqual(projected['sources'][0],body['sources'][0])
        self.assert_roundtrip(view,raw)

    async def test_new_successful_fetch_body_and_calendar_exceptions_are_not_removed(self):
        await self.search();self.api.fail=False
        body,card=await self.fetch()
        view=self.project()
        projected=json.loads(view[-1]['content'])
        self.assertEqual(projected['content'],body['content'])
        self.assertIn('university holidays closed',projected['content'])
        self.assertEqual(projected['sources'],body['sources'])
        self.assertEqual(projected['sources'][0]['claim_kind'],'fetched_page')
        self.assertIsNone(card.conclusion)
        self.assert_roundtrip(view,self.messages)

    async def test_closed_or_absent_card_channel_keeps_full_details_and_links(self):
        await self.search();await self.fetch()
        await self.sender.close()
        view=self.project()
        for old,new in zip(self.messages[2:],view[2:],strict=True):
            expected=json.loads(old['content']);actual=json.loads(new['content'])
            expected.pop('response_presentation');hint=actual.pop('response_presentation')
            self.assertEqual(actual,expected)
            self.assertEqual(hint['source_details'],'voice_only')
            self.assertEqual(hint['card_delivery'],'not_available')
        self.assertEqual(json.loads(view[-1]['content'])['sources'],self.body['sources'])

    async def test_explicit_detailed_or_link_reading_has_all_original_evidence_once(self):
        await self.search();await self.fetch()
        detailed=deepcopy(self.messages)
        detailed[1]['content']='详细逐条读给我，包括全部网址和例外，也请比较不同时间。'
        view=self.project(detailed)
        self.assert_roundtrip(view,detailed)
        for message in view[2:]:
            expanded=expand_evidence(view,message)
            self.assertEqual(expanded['sources'],json.loads(detailed[view.index(message)]['content'])['sources'])
        self.assertEqual(view[1],detailed[1])

    async def test_no_reference_across_new_user_or_cancel(self):
        await self.search();await self.fetch()
        for boundary in [{'role':'user','content':'A new synthetic user turn'},
                         {'role':'tool','tool_call_id':'cancel-call','content':encoded({'accepted':False,'status':'cancelled'})}]:
            with self.subTest(boundary=boundary['role']):
                raw=[*self.messages[:3],boundary,self.messages[-1]]
                view=self.project(raw)
                self.assertEqual(json.loads(view[-1]['content'])['sources'],self.body['sources'])

    async def test_foreign_session_or_turn_scope_does_not_reuse_same_numbered_ids(self):
        await self.search();await self.fetch()
        raw=deepcopy(self.messages)
        previous=json.loads(raw[2]['content'])
        previous['response_presentation']['evidence_scope']='different-identity-or-request-scope'
        raw[2]['content']=encoded(previous)
        view=self.project(raw)
        self.assertEqual(json.loads(view[-1]['content'])['sources'],self.body['sources'])
        self.assert_roundtrip(view,raw)

    async def test_source_assertions_empty_or_populated_remain_exact_and_do_not_upgrade(self):
        await self.search();await self.fetch()
        for assertions in [[],[{'source_id':self.body['sources'][0]['source_id'],
                               'quote':self.body['sources'][0]['excerpt'],'claim':'planned, not confirmed'}]]:
            with self.subTest(assertions=bool(assertions)):
                raw=deepcopy(self.messages)
                for message in raw[2:]:
                    body=json.loads(message['content']);body['evidence_reading']['source_assertions']=assertions
                    message['content']=encoded(body)
                view=self.project(raw)
                self.assert_roundtrip(view,raw)
                self.assertEqual(json.loads(view[-1]['content'])['evidence_reading']['source_assertions'],assertions)
                self.assertIsNone(json.loads(view[-1]['content'])['conclusion'])

    async def test_corrupted_reference_source_id_or_hash_is_rejected(self):
        await self.search();await self.fetch()
        view=self.project()
        for key,value in [('source_id','some-other-source'),('sha256','0'*64),('tool_call_id','absent-tool')]:
            raw=deepcopy(view);body=json.loads(raw[-1]['content'])
            body['sources'][0]['evidence_ref'][key]=value;raw[-1]['content']=encoded(body)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'no visible complete source'):
                self.project(raw)

    async def test_empty_wrong_type_or_renamed_outer_source_reference_fails_closed(self):
        await self.search();await self.fetch()
        view=self.project()
        for ref in [None,{},False,[],{'source_id':'x','sha256':[],'tool_call_id':'search-call'}]:
            raw=deepcopy(view);body=json.loads(raw[-1]['content'])
            body['sources'][0]['evidence_ref']=ref;raw[-1]['content']=encoded(body)
            with self.subTest(ref=ref),self.assertRaisesRegex(ValueError,'no visible complete source'):
                self.project(raw)
        raw=deepcopy(view);body=json.loads(raw[-1]['content'])
        body['sources'][0]['source_id']='a-renamed-source';raw[-1]['content']=encoded(body)
        with self.assertRaisesRegex(ValueError,'no visible complete source'):
            self.project(raw)
