"""Dialogue policy/data flow only: scripted text is not model-quality evidence."""
import json
import unittest
from her_agent.author import reading_sections
from her_agent.provider import TextDelta
from her_agent.runtime import ConversationRuntime
from her_agent.voice import VoiceBridge
from her_agent.web_lookup import WebLookup, WebFailure
from test_lookup import CLOCK, PACK, Scripted, call


class PublicFixture:
    def __init__(self, fail=False): self.requests=[];self.fail=fail
    async def search(self,query):
        self.requests.append(('search',query.text()))
        return {'results':[{'title':'Synthetic public visit fixture','url':'https://example.org/visit',
                            'summary':'Hypothetical regular hours10–17; exceptions not confirmed.'}]}
    async def fetch(self,url):
        self.requests.append(('fetch',url))
        if self.fail:raise WebFailure('synthetic unavailable')
        return {'content':'Synthetic visit fixture: hours10–17; closed for a stated maintenance exception.'}


class JoDialogueFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self,user,rounds,*,fail=False,history=None):
        api=PublicFixture(fail);lookup=WebLookup(api,CLOCK);actor=Scripted();actor.rounds=rounds
        runtime=ConversationRuntime(PACK,actor,lookup=lookup);VoiceBridge(runtime)
        if history is not None:runtime.history=history
        try:
            turn=runtime.begin(user);before=json.dumps(turn.messages,ensure_ascii=False)
            text=''.join([p async for p in runtime.stream(turn)])
            self.assertEqual(json.dumps(turn.messages,ensure_ascii=False),before,'do not replace author/user/history')
            runtime.delivered(turn,text,complete=True)
            return actor,api,turn,text
        finally:await runtime.close(flush=False)

    async def test_normal_chat_keeps_tools_available_without_forcing_a_lookup(self):
        user='今天忙了一整天，只想找个人随便聊两句。'
        actor,api,_,text=await self.run_turn(user,[[TextDelta('合成接线占位，不是模型对白。')]])
        self.assertEqual(api.requests,[])
        names={t['function']['name'] for t in actor.tool_schemas[0]}
        self.assertTrue({'web_search','web_fetch'}<=names)
        self.assertEqual(actor.sent[0][-1]['content'],user)

    async def test_personal_preference_keeps_role_material_and_does_not_require_search(self):
        user='你自己更喜欢安静还是热闹的地方？我想听你的看法。'
        actor,api,turn,_=await self.run_turn(user,[[TextDelta('合成个人偏好接线占位。')]])
        self.assertEqual(api.requests,[])
        for document in PACK.documents:
            for section in reading_sections(document.body):
                self.assertIn(section,actor.sent[0][0]['content'])
        self.assertEqual(actor.sent[0][-1]['content'],user)

    async def test_explicit_current_hours_request_can_still_search_and_fetch(self):
        user='请帮我核对NHM明天下午开放到几点，也看看临时闭馆信息。'
        rounds=[[call('web_search',{'area':'usc_exposition','topic':'visit_hours','place_id':'place.nhm'},'s')],
                [call('web_fetch',{'result_id':'rq-2-0'},'f')],[TextDelta('合成结果接线占位。')]]
        actor,api,turn,_=await self.run_turn(user,rounds)
        self.assertEqual([r[0] for r in api.requests],['search','fetch'])
        body=json.loads(actor.sent[-1][-1]['content'])
        self.assertEqual(body['sources'][0]['claim_kind'],'fetched_page')
        self.assertEqual(body['evidence_reading']['fact_verification'],'not_performed')
        self.assertIsNone(turn.source_card['conclusion'])

    async def test_fetch_failure_keeps_uncertainty_and_source_grades(self):
        rounds=[[call('web_search',{'area':'usc_exposition','topic':'visit_hours','place_id':'place.nhm'},'s')],
                [call('web_fetch',{'result_id':'rq-2-0'},'f')],[TextDelta('合成失败接线占位。')]]
        actor,api,turn,_=await self.run_turn('请核对这个场馆现在能不能去。',rounds,fail=True)
        body=json.loads(actor.sent[-1][-1]['content'])
        self.assertEqual([r[0] for r in api.requests],['search','fetch'])
        self.assertEqual(body['status'],'unavailable');self.assertEqual(body['page_read_status'],'unavailable')
        self.assertTrue(all(s['claim_kind']=='search_summary' for s in body['sources']))
        self.assertEqual(body['evidence_reading']['source_assertions'],[])
        self.assertEqual(body['evidence_reading']['fact_verification'],'not_performed')
        self.assertIsNone(turn.source_card['conclusion'])

    async def test_topic_change_does_not_replay_old_tool_protocol(self):
        history=[{'role':'user','content':'刚才想查场馆时间。'},
                 {'role':'assistant','content':'合成旧回答：上题尚未确认。'}]
        user='先不聊那个了。你听歌的时候更注意声音还是词？'
        actor,api,_,_=await self.run_turn(user,[[TextDelta('合成新话题接线占位。')]],history=history)
        self.assertEqual(api.requests,[])
        self.assertFalse(any(m['role']=='tool' for m in actor.sent[0]))
        self.assertEqual(actor.sent[0][-1]['content'],user)
