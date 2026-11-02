"""Voice/card division through the existing actor loop, never semantic scores."""
import json
import hashlib
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from angeleno.lookup import VOICE_PRESENTATION
from angeleno.memory_tools import request_chars
from angeleno.provider import TextDelta
from angeleno.runtime import ConversationRuntime
from angeleno.voice import VoiceBridge
from angeleno.voice_cards import VoiceCards
from angeleno.web_lookup import RecordedWebResult, WebFailure, WebLookup
from test_lookup import CLOCK, PACK, Scripted, call

FIXTURE=json.loads((Path(__file__).resolve().parents[1]/'examples/voice-presentation.development.json').read_text())


def expand_evidence(messages, message):
    """Independently resolve every reference against the actual supplied request."""
    value=json.loads(message['content'])
    def resolve(ref):
        origin=next(m for m in messages if m.get('tool_call_id')==ref['tool_call_id'])
        source=next(s for s in json.loads(origin['content'])['sources']
                    if s['source_id']==ref['source_id'] and 'excerpt' in s
                    and hashlib.sha256(json.dumps(s,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==ref['sha256'])
        return source
    for i,source in enumerate(value.get('sources',[])):
        if 'evidence_ref' in source:value['sources'][i]=resolve(source['evidence_ref'])
    for candidate in value.get('results',[]):
        if 'evidence_ref' in candidate:
            source=resolve(candidate.pop('evidence_ref'))
            candidate.update(summary=source['excerpt'],url=source['url'])
    return value


class PublicReplay:
    def __init__(self, fail=True):self.fail=fail;self.calls=[]
    async def search(self, query):
        self.calls.append('search')
        return RecordedWebResult({'results':[{'title':x['name'],'url':x['url'],'summary':x['excerpt']}
            for x in FIXTURE['recorded_sources']]},FIXTURE['recorded_at'],FIXTURE['source_artifact_sha256'])
    async def fetch(self,url):
        self.calls.append('fetch')
        if self.fail:raise WebFailure('web_unavailable')
        return RecordedWebResult({'content':'Public test page: Tuesday–Saturday 10:00–17:00; university holidays closed.'},
                                 FIXTURE['recorded_at'],'a'*64)


class VoicePresentationTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, case, *, voice=True, closed=False, spoken='合成接线占位，不是模型输出。'):
        api=PublicReplay(fail=case['fetch']=='failed');lookup=WebLookup(api,CLOCK);actor=Scripted()
        actor.rounds=[[call('web_search',{'area':'usc_exposition','topic':'visit_hours','place_id':'place.fisher'},'search')],
                      [call('web_fetch',{'result_id':'rq-2-0'},'fetch')],[TextDelta(spoken)]]
        runtime=ConversationRuntime(PACK,actor,lookup=lookup);sender=None
        if voice:VoiceBridge(runtime)
        if case['card_channel']:
            participant=Mock();participant.publish_data=AsyncMock()
            sender=VoiceCards(lookup,participant,'synthetic-recipient')
            if closed:await sender.close()
        try:
            turn=runtime.begin(case['user']);original=json.dumps(turn.messages,ensure_ascii=False)
            text=''.join([s async for s in runtime.stream(turn)])
            runtime.delivered(turn,text,complete=True)
            self.assertEqual(json.dumps(turn.messages,ensure_ascii=False),original,'presentation must not rewrite author/history')
            self.assertEqual(api.calls,['search','fetch'])
            self.assertEqual(len(actor.sent),3,'no added model stage')
            return actor.sent, text, turn.source_card
        finally:
            if sender:await sender.close()
            await runtime.close(flush=False)

    async def test_card_failure_keeps_lossless_evidence_and_deduplicates_voice_view(self):
        case=FIXTURE['cases'][0]
        before,_,_=await self.capture(case,voice=False)
        after,_,card=await self.capture(case)
        self.assertNotIn(VOICE_PRESENTATION,before[-1][0]['content'])
        self.assertEqual(after[-1][0]['content'],before[-1][0]['content']+'\n'+VOICE_PRESENTATION)
        self.assertEqual(card['status'],'unavailable');self.assertIsNone(card['conclusion'])
        for old,new in zip(before[-1][1:],after[-1][1:]):
            if old['role']=='tool':
                a=json.loads(old['content']);b=expand_evidence(after[-1],new);hint=b.pop('response_presentation')
                self.assertEqual(a,b,'facts, excerpts, dates, IDs and source grades are not shortened')
                self.assertEqual(hint['source_details'],'separate_card')
                self.assertEqual(hint['card_delivery'],'not_confirmed')
            else:self.assertEqual(old,new)
        self.assertTrue(all(c['claim_kind']=='search_summary' for c in card['citations']))
        search=json.loads(after[-1][3]['content']);failed=json.loads(after[-1][5]['content'])
        self.assertTrue(all('summary' not in c and 'url' not in c for c in search['results']))
        self.assertTrue(all('excerpt' in c and 'url' in c for c in search['sources']))
        self.assertTrue(all('evidence_ref' in c and 'excerpt' not in c and 'url' not in c for c in failed['sources']))

    async def test_seen_w08_failure_prefix_is_not_filtered_or_cut(self):
        self.assertFalse(FIXTURE['observed_prefix_complete_reply'])
        _,spoken,_=await self.capture(FIXTURE['cases'][0],spoken=FIXTURE['observed_prefix'])
        self.assertEqual(spoken,FIXTURE['observed_prefix'],'offline wiring cannot make bad model output pass by stripping it')

    async def test_current_explicit_detail_request_remains_available(self):
        case=FIXTURE['cases'][1];detail='逐条合成说明。'*100
        sent,spoken,_=await self.capture(case,spoken=detail)
        self.assertEqual(spoken,detail)
        self.assertEqual(next(m['content'] for m in reversed(sent[-1]) if m['role']=='user'),case['user'])
        self.assertIn('用户明确要详细听取或比较时展开',sent[-1][0]['content'])
        self.assertIn('保留相关时间、例外和冲突',sent[-1][0]['content'])

    async def test_no_card_and_closed_card_never_claim_separate_visible_card(self):
        for case,closed in [(FIXTURE['cases'][2],False),(FIXTURE['cases'][0],True)]:
            with self.subTest(case=case['id'],closed=closed):
                sent,_,_=await self.capture(case,closed=closed)
                hints=[json.loads(m['content'])['response_presentation'] for m in sent[-1] if m['role']=='tool']
                self.assertTrue(all(h['source_details']=='voice_only' and h['card_delivery']=='not_available' for h in hints))
                self.assertIn('不假称有可见卡片',sent[-1][0]['content'])

    async def test_read_page_and_failed_fetch_keep_different_source_grades(self):
        read,_,card=await self.capture(FIXTURE['cases'][2])
        self.assertEqual(card['citations'][0]['claim_kind'],'fetched_page');self.assertIsNone(card['conclusion'])
        failed,_,card=await self.capture(FIXTURE['cases'][3])
        body=json.loads(failed[-1][-1]['content'])
        self.assertEqual(body['page_read_status'],'unavailable')
        self.assertEqual(body['evidence_reading']['source_assertions'],[])
        self.assertEqual(card['status'],'unavailable');self.assertIsNone(card['conclusion'])
        self.assertIn('evidence仅表示取得资料，绝不等于结论已核实',failed[-1][0]['content'])

    async def test_voice_rules_are_included_in_existing_request_budget(self):
        lookup=WebLookup(PublicReplay(),CLOCK);actor=Scripted();runtime=ConversationRuntime(PACK,actor,lookup=lookup)
        VoiceBridge(runtime)
        try:
            turn=runtime.begin(FIXTURE['cases'][0]['user'])
            old_limit=request_chars(actor,lookup.prepare_messages(turn.messages),lookup.tool_definitions())
            with patch('angeleno.lookup.MAX_REQUEST_CHARS',old_limit):
                with self.assertRaisesRegex(ValueError,'input budget'):
                    _=[x async for x in runtime.stream(turn)]
            self.assertEqual(actor.sent,[])
        finally:await runtime.close(flush=False)
