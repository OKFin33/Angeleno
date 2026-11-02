"""Opt-in commit gate with synthetic stores/fake same-writer judgments only.

A correct fake judgment tests enforcement, not actual semantic reliability.
"""
import asyncio
from copy import deepcopy
import json
from unittest.mock import patch
import unittest

from angeleno.attribution import CHECK, prepare_check
from angeleno.continuity import Conversation, Maintainer
from angeleno.runtime import ConversationRuntime
from test_memory import FakeProvider, StoreFixture, record


def judgments(payload, *, verdict='supported', issue=None, scope='user_claims'):
    users=[e for e in payload['sources'] if e['role']==('assistant' if scope=='no_user_claims' else 'user')]
    evidence=[{'seq':users[-1]['seq'],'quote':users[-1]['text']}] if users else []
    return {'checks':[{'id':c['id'],'digest':c['digest'],'scope':scope,'verdict':verdict,
                      'evidence':deepcopy(evidence),'issue':None if verdict=='supported' else {
                          'quote':issue or c['clauses'][0],'reason':'合成判断：用户原话没有明确支持这个范围。'}}
                      for c in payload['candidates']]}


class TwoStage(FakeProvider):
    def __init__(self, proposal, check=None, *, hold=False, suppress_cancel=False):
        self.proposal=proposal;self.check=check or judgments
        self.check_started=asyncio.Event();self.release=asyncio.Event()
        self.hold=hold;self.suppress_cancel=suppress_cancel;self.requests=[];self.closed=False

    async def chat(self,messages,**kwargs):
        self.requests.append(deepcopy(messages))
        payload=json.loads(messages[-1]['content'])
        if 'batch' in payload:value=self.proposal
        else:
            self.check_started.set()
            if self.hold:
                try:await self.release.wait()
                except asyncio.CancelledError:
                    if not self.suppress_cancel:raise
            value=self.check(payload)
        if isinstance(value,Exception):raise value
        yield value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)

    async def aclose(self):self.closed=True


class AttributionCandidateTests(StoreFixture,unittest.IsolatedAsyncioTestCase):
    def candidate(self,proposal,check=None,**kwargs):
        p=TwoStage(proposal,check,**kwargs)
        return Maintainer(self.store,self.pack,p,attribution_check=True),p

    def snapshot(self):
        return (self.store.records(include_hidden=True),self.store.watermark,self.store.revision,
                [tuple(x) for x in self.store.db.execute('SELECT * FROM terms ORDER BY record_id,term')],self.store.pending())

    async def test_ambiguous_rejection_blocks_whole_proposal_and_bad_keywords(self):
        old=self.exchange('我喜欢安静。');self.apply(record('card.user','用户喜欢安静。',[old[0]],kind='user',keywords=['安静']))
        self.exchange('有哪些选择？','可以往Downtown找，或者看建筑开放日。')
        ev=self.exchange('这个我不太喜欢，能说说你为什么会选它？','你是说两个方向都不喜欢吗？')
        bad=record('card.user','用户不太喜欢这两个方向。',[ev[0]],kind='user',keywords=['拒绝两个方向'])
        before=self.snapshot();m,p=self.candidate({'retire':[],'updates':[bad]},lambda v:judgments(v,verdict='unconfirmed',issue='这两个方向'))
        self.assertFalse(await m.once());self.assertEqual(m.error,'AttributionRejected')
        self.assertEqual(self.snapshot(),before);self.assertEqual(len(p.requests),2)
        self.assertEqual(m.last_attempt['attribution_result']['checks'][0]['verdict'],'unconfirmed')
        m.schedule();await m.flush();self.assertEqual(len(p.requests),2,'no automatic retry after rejection')
        self.assertIn('这个我不太喜欢',str(Conversation(self.pack,self.store).begin('继续')))

    async def test_unaccepted_suggestion_does_not_become_user_interest(self):
        ev=self.exchange('我更想看看建筑和空间。','Fisher是一个建筑主题选项，你自己决定。')
        bad=record('card.active','用户对Fisher有兴趣但尚未决定去。',ev,kind='active',keywords=['Fisher兴趣'])
        before=self.snapshot();m,p=self.candidate({'retire':[],'updates':[bad]},lambda v:judgments(v,verdict='unconfirmed',issue='用户对Fisher有兴趣'))
        self.assertFalse(await m.once());self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.store.records(),[])
        self.assertIn('Fisher',json.loads(p.requests[1][1]['content'])['sources'][-1]['text'])

    async def test_unsupported_keyword_alone_cannot_enter_retrieval_index_after_rejection(self):
        ev=self.exchange('我只是问问建筑空间，不是说想去Fisher。')
        value=record('card.user','用户问建筑空间，未表示想去。',[ev[0]],kind='user',keywords=['Fisher兴趣'])
        m,p=self.candidate({'retire':[],'updates':[value]},lambda v:judgments(v,verdict='conflict',issue='Fisher兴趣'))
        before=self.snapshot();self.assertFalse(await m.once());self.assertEqual(self.snapshot(),before)
        self.assertEqual(json.loads(p.requests[1][1]['content'])['candidates'][0]['keywords'],['Fisher兴趣'])

    async def test_explicit_both_rejection_and_Fisher_interest_are_not_blanket_blocked(self):
        for text,summary,kind in [('这两个方向我都不喜欢。','用户明确不喜欢这两个方向。','user'),
                                  ('我对Fisher这个展有兴趣，先保留这个选项。','用户对Fisher有兴趣，先保留这个选项。','active')]:
            ev=self.exchange(text)
            proposed=record('card.'+kind,summary,[ev[0]],kind=kind,keywords=[summary])
            m,p=self.candidate({'retire':[],'updates':[proposed]})
            self.assertTrue(await m.once());self.assertEqual(len(p.requests),2)
            row=next(r for r in self.store.records() if r['id']==proposed['id'])
            self.assertTrue(all(row[k]==v for k,v in proposed.items()))

    async def test_new_explicit_correction_replaces_old_preference_without_touching_author_past(self):
        old=self.exchange('先只看小空间。');self.apply(record('card.user','用户暂时只看小空间。',[old[0]],kind='user'))
        fresh=self.exchange('大展馆不代表完全不去，喜欢的建筑我也会去。','我上次看那个建筑展看到一半被叫走。')
        corrected=record('card.user','用户纠正：大展馆不是完全不去，喜欢的建筑也会去。',[fresh[0]],kind='user')
        past=record('card.character','角色说自己上次看展看到一半被叫走。',[fresh[1]],kind='character')
        m,p=self.candidate({'retire':['card.user'],'updates':[corrected,past]})
        self.assertTrue(await m.once());self.assertEqual(len(p.requests),2)
        payload=json.loads(p.requests[1][1]['content'])
        self.assertEqual([c['id'] for c in payload['candidates']],['card.user'])
        self.assertNotIn('author_material',payload);self.assertNotIn('batch',payload)
        self.assertNotIn('林是一名成年画框修复师',str(p.requests[1]))
        self.assertEqual({r['id'] for r in self.store.records()},{'card.user','card.character'})
        self.assertEqual(self.store.watermark,fresh[-1])

    async def test_character_only_and_noop_keep_original_single_writer_path(self):
        ev=self.exchange('讲讲你自己的过去。','我以前在装裱店帮忙。')
        m,p=self.candidate({'retire':[],'updates':[record('card.character','角色说以前在装裱店帮忙。',[ev[1]],kind='character')]})
        self.assertTrue(await m.once());self.assertEqual(len(p.requests),1)
        self.exchange('谢谢。');m,p=self.candidate({'retire':[],'updates':[]})
        self.assertTrue(await m.once());self.assertEqual(len(p.requests),1)
        self.assertEqual(self.store.pending(),[])

    async def test_episode_role_past_is_not_subject_to_author_truth_rejudgment(self):
        ev=self.exchange('后来呢？','我上次看展看到一半被叫走了。')
        episode=record('episode.role-past','角色说上次看到一半被叫走。',[ev[1]])
        m,p=self.candidate({'retire':[],'updates':[episode]},lambda v:judgments(v,scope='no_user_claims'))
        self.assertTrue(await m.once());self.assertEqual(self.store.records()[0]['text'],episode['text'])

    async def test_default_off_does_not_change_production_call_count(self):
        ev=self.exchange('我喜欢安静。');proposed=record('card.user','用户喜欢安静。',[ev[0]],kind='user')
        p=TwoStage({'retire':[],'updates':[proposed]});m=Maintainer(self.store,self.pack,p)
        self.assertTrue(await m.once());self.assertEqual(len(p.requests),1)

    async def test_bad_verdicts_or_quotes_reject_without_rewriting_any_candidate(self):
        ev=self.exchange('我想看建筑。');proposal={'retire':[],'updates':[record('card.user','用户想看建筑。',[ev[0]],kind='user')]}
        def alter(fn):
            def build(v):r=judgments(v);fn(r);return r
            return build
        cases=[lambda v:'not JSON',lambda v:{'checks':[]},
               alter(lambda r:r['checks'][0].update(digest='0'*64)),
               alter(lambda r:r['checks'].append(deepcopy(r['checks'][0]))),
               alter(lambda r:r['checks'][0].update(scope='no_user_claims')),
               alter(lambda r:r['checks'][0]['evidence'][0].update(quote='没有说过的话')),
               alter(lambda r:r['checks'][0].update(repaired_text='invented correction'))]
        before=self.snapshot()
        for check in cases:
            m,p=self.candidate(proposal,check)
            self.assertFalse(await m.once());self.assertEqual(self.snapshot(),before);self.assertEqual(len(p.requests),2)

    async def test_assistant_quote_cannot_structurally_support_a_user_fact(self):
        ev=self.exchange('你怎么看？','我猜你喜欢建筑。')
        proposed=record('card.active','用户喜欢建筑。',ev,kind='active')
        def fake(v):
            r=judgments(v);e=next(e for e in v['sources'] if e['role']=='assistant')
            r['checks'][0]['evidence']=[{'seq':e['seq'],'quote':e['text']}];return r
        m,p=self.candidate({'retire':[],'updates':[proposed]},fake);before=self.snapshot()
        self.assertFalse(await m.once());self.assertEqual(self.snapshot(),before)

    async def test_unsupported_user_card_retirement_does_not_delete_old_confirmed_card(self):
        ev=self.exchange('我喜欢安静。');self.apply(record('card.user','用户喜欢安静。',[ev[0]],kind='user'))
        self.exchange('换个话题。')
        m,p=self.candidate({'retire':['card.user'],'updates':[]},lambda v:judgments(v,verdict='unconfirmed',issue='用户喜欢安静'))
        before=self.snapshot();self.assertFalse(await m.once());self.assertEqual(self.snapshot(),before)

    async def test_compact_input_budget_never_clips_sources_or_sends_second_call(self):
        ev=self.exchange('原始事实必须完整。','必要语境。')
        proposal={'retire':[],'updates':[record('card.user','用户说原始事实必须完整。',[ev[0]],kind='user')]}
        m,p=self.candidate(proposal);before=self.snapshot()
        with patch('angeleno.attribution.MAX_CHECK_CHARS',10):self.assertFalse(await m.once())
        self.assertEqual(len(p.requests),1);self.assertEqual(self.snapshot(),before)

    async def test_rejection_can_be_resumed_explicitly_without_permanent_summary_ban(self):
        ev=self.exchange('这个不太喜欢。','我猜你拒绝两个。')
        bad=record('card.user','用户拒绝两个方向。',[ev[0]],kind='user')
        m,p=self.candidate({'retire':[],'updates':[bad]},lambda v:judgments(v,verdict='unconfirmed',issue='两个方向'))
        self.assertFalse(await m.once());pending=self.store.pending()
        self.store.close();self.store=self.open();self.assertEqual(self.store.pending(),pending)
        correct=record('card.user','用户说这个不太喜欢；所指尚未明确。',[ev[0]],kind='user')
        m2,p2=self.candidate({'retire':[],'updates':[correct]})
        self.assertTrue(await m2.once());self.assertEqual(self.store.pending(),[])
        self.assertEqual(self.store.records()[0]['text'],correct['text'])

    async def test_timeout_or_cancel_keeps_raw_events_and_watermark_after_reopen(self):
        for suppress in (False,True):
            ev=self.exchange('我喜欢安静。')
            proposal={'retire':[],'updates':[record('card.user','用户喜欢安静。',[ev[0]],kind='user')]}
            m,p=self.candidate(proposal,hold=True,suppress_cancel=suppress);before=self.snapshot()
            with self.assertRaises(TimeoutError):await asyncio.wait_for(m.once(),.02)
            self.assertEqual(self.snapshot(),before);self.assertEqual(len(p.requests),2)
            m.schedule();await m.flush();self.assertEqual(len(p.requests),2)
            self.store.close();self.store=self.open();self.assertEqual(self.snapshot(),before)

    async def test_second_call_has_only_remaining_total_maintenance_budget(self):
        ev=self.exchange('我喜欢安静。')
        proposed={'retire':[],'updates':[record('card.user','用户喜欢安静。',[ev[0]],kind='user')]}
        class Slow(TwoStage):
            timeout_s=.04
            async def chat(inner,messages,**kwargs):
                await asyncio.sleep(.025)
                async for piece in super().chat(messages,**kwargs):yield piece
        p=Slow(proposed);m=Maintainer(self.store,self.pack,p,attribution_check=True)
        before=self.snapshot();self.assertFalse(await m.once())
        self.assertEqual(m.error,'TimeoutError');self.assertEqual(self.snapshot(),before)
        self.assertEqual(len(p.requests),1,'second stage delay must not get a new full budget')

    async def test_forget_during_check_cannot_commit_late_or_reintroduce_deleted_sources(self):
        ev=self.exchange('不应再出现的合成词。')
        m,p=self.candidate({'retire':[],'updates':[record('card.user','用户说不应再出现的合成词。',[ev[0]],kind='user')]},hold=True)
        m.schedule();await p.check_started.wait();self.store.forget();p.release.set();await m.task
        self.assertEqual(m.error,'StaleBatch');self.assertEqual(self.store.records(),[]);self.assertEqual(self.store.pending(),[])

    async def test_forget_before_second_stage_does_not_disclose_forgotten_sources_again(self):
        ev=self.exchange('只用于合成遗忘反例。')
        proposed={'retire':[],'updates':[record('card.user','用户说只用于合成遗忘反例。',[ev[0]],kind='user')]}
        def first(_):self.store.forget();return proposed
        p=FakeProvider(first);m=Maintainer(self.store,self.pack,p,attribution_check=True)
        self.assertFalse(await m.once());self.assertEqual(m.error,'StaleBatch')
        self.assertEqual(len(p.requests),1);self.assertEqual(self.store.records(),[])

    async def test_close_uses_one_original_caller_budget_and_preserves_unprocessed_events(self):
        ev=self.exchange('我喜欢安静。')
        p=TwoStage({'retire':[],'updates':[record('card.user','用户喜欢安静。',[ev[0]],kind='user')]},hold=True)
        actor=FakeProvider(lambda p:'unused')
        runtime=ConversationRuntime(self.pack,actor,store=self.store,writer=p)
        runtime.maintainer=Maintainer(self.store,self.pack,p,attribution_check=True)
        runtime.completed=1
        before=self.store.pending();await runtime.close(timeout=.02)
        self.assertEqual(runtime.maintainer.error,'TimeoutError');self.assertTrue(p.closed)
        self.store=self.open();self.assertEqual(self.store.pending(),before);self.assertEqual(self.store.records(),[])
        self.assertEqual(len(p.requests),2)

    async def test_temporary_no_save_produces_no_attribution_or_writer_request(self):
        class Actor:
            async def chat(self,*a,**kw):yield '合成回应。'
            async def aclose(self):pass
        p=TwoStage({'retire':[],'updates':[]});runtime=ConversationRuntime(self.pack,Actor(),store=self.store,writer=p)
        runtime.maintainer=Maintainer(self.store,self.pack,p,attribution_check=True);runtime.set_temporary(True)
        for _ in range(3):
            turn=runtime.begin('本段不要保存。');text=''.join([s async for s in runtime.stream(turn)]);runtime.delivered(turn,text,complete=True)
        self.assertFalse(await runtime.maintainer.once());self.assertEqual(p.requests,[]);self.assertEqual(self.store.pending(),[])
        await runtime.close(flush=False);self.store=self.open();self.assertEqual(self.store.records(),[])

    async def test_quote_validation_is_not_semantic_entailment_and_false_positive_is_possible(self):
        ev=self.exchange('我只是在问你为什么选它。','先聊聊理由。')
        bad=record('card.user','用户决定周六去看展。',[ev[0]],kind='user')
        m,p=self.candidate({'retire':[],'updates':[bad]})  # deliberately WRONG fake supported verdict
        self.assertTrue(await m.once())
        self.assertEqual(self.store.records()[0]['text'],bad['text'])
        self.assertEqual(m.last_attempt['attribution_result']['checks'][0]['verdict'],'supported')
        # Explicitly demonstrates remaining semantic-model uncertainty; not a
        # claim that the false record is correct or the candidate is accepted.



class DirectEvidenceTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    """Direct complete assistant citations survive projection, without user endorsement."""

    def snapshot(self):
        return '\n'.join(self.store.db.iterdump())

    async def test_assistant_only_batch_can_keep_delivered_role_past(self):
        self.store.append('synthetic', 'user', '讲讲你自己的过去。')
        self.store.apply(self.store.batch(), {'updates': [], 'retire': []})
        seq = self.store.append('synthetic', 'assistant', '我上次看展只看了一半就被叫走。')
        proposed = record('episode.role-past', '角色说上次看展只看了一半就被叫走。', [seq])
        provider = TwoStage({'updates': [proposed], 'retire': []},
                            lambda payload: judgments(payload, scope='no_user_claims'))
        maintainer = Maintainer(self.store, self.pack, provider, attribution_check=True)
        self.assertTrue(await maintainer.once())
        payload = json.loads(provider.requests[1][1]['content'])
        self.assertEqual([(s['seq'], s['role'], s['text']) for s in payload['sources']],
                         [(seq, 'assistant', '我上次看展只看了一半就被叫走。')])
        stored = self.store.records()[0]
        self.assertEqual({k: stored[k] for k in proposed}, proposed)
        self.assertEqual(self.store.pending(), [])

    async def test_direct_old_assistant_from_other_session_is_available_unchanged(self):
        old = self.exchange('以前呢？', '我以前在装裱店帮忙。', session='earlier-synthetic')
        self.apply(record('episode.role-past', '角色说以前在装裱店帮忙。', [old[1]], keywords=['装裱店']))
        fresh = self.exchange('装裱店那段再说一点。', '我说的是以前的事，不是这周的近况。', session='current-synthetic')
        proposed = record('episode.role-past', '角色说以前在装裱店帮忙；后来说明这不是这周的近况。',
                          [old[1], fresh[1]], keywords=['装裱店'])
        def check(payload):
            out = judgments(payload, scope='no_user_claims')
            source = next(s for s in payload['sources'] if s['seq'] == old[1])
            out['checks'][0]['evidence'] = [{'seq': source['seq'], 'quote': source['text']}]
            return out
        provider = TwoStage({'updates': [proposed], 'retire': []}, check)
        maintainer = Maintainer(self.store, self.pack, provider, attribution_check=True)
        self.assertTrue(await maintainer.once())
        payload = json.loads(provider.requests[1][1]['content'])
        prior = next(s for s in payload['sources'] if s['seq'] == old[1])
        self.assertEqual(prior['text'], '我以前在装裱店帮忙。')
        self.assertFalse(prior['new_in_batch'])
        self.assertNotIn('session', prior)
        self.assertNotIn('author_material', payload)

    async def test_direct_assistant_does_not_expand_its_unrelated_neighbors(self):
        from types import SimpleNamespace
        def event(seq, role, text, session):
            return {'seq': seq, 'role': role, 'text': text, 'session': session,
                    'created': '2026-10-30T22:04:05+00:00', 'delivery': 'complete'}
        old = [event(1, 'user', '无关的旧用户话。', 'old'),
               event(2, 'assistant', '角色直接说过的完整过去。', 'old'),
               event(3, 'assistant', '无关的旧角色段落。', 'old')]
        fresh = event(10, 'user', '继续。', 'current')
        batch = SimpleNamespace(records=(), events=(fresh,), watermark=9, through=10)
        proposed = record('episode.role-past', '角色说过完整过去。', [2, 10])
        _, payload = prepare_check({'updates': [proposed], 'retire': []}, batch, old)
        self.assertEqual([s['seq'] for s in payload['sources']], [2, 10])
        self.assertEqual(payload['sources'][0]['text'], old[1]['text'])

    async def test_interrupted_or_absent_direct_assistant_rejects_without_second_call_or_state_change(self):
        old = self.exchange('我喜欢安静。')
        self.apply(record('card.user', '用户喜欢安静。', [old[0]], kind='user'))
        self.store.append('synthetic', 'user', '后来呢？')
        interrupted = self.store.append('synthetic', 'assistant', '没有完整交付的角色前缀。', delivery='interrupted')
        before = self.snapshot()
        for seq in (interrupted, interrupted + 100):
            proposed = record('episode.incomplete', '角色说有新的过去。', [seq])
            provider = TwoStage({'updates': [proposed], 'retire': []})
            maintainer = Maintainer(self.store, self.pack, provider, attribution_check=True)
            self.assertFalse(await maintainer.once())
            self.assertEqual(maintainer.error, 'ValueError')
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(self.snapshot(), before)

    async def test_direct_assistant_cannot_stand_in_for_user_confirmation(self):
        old = self.exchange('我喜欢安静。')
        self.apply(record('card.user', '用户喜欢安静。', [old[0]], kind='user'))
        self.store.append('synthetic', 'user', '你怎么看？')
        self.store.apply(self.store.batch(), {'updates': [], 'retire': []})
        seq = self.store.append('synthetic', 'assistant', '我猜你对Fisher有兴趣。')
        proposed = record('card.active', '用户对Fisher有兴趣。', [seq], kind='active')
        def check(payload):
            out = judgments(payload, scope='no_user_claims')
            out['checks'][0]['scope'] = 'user_claims'
            return out
        before = self.snapshot()
        provider = TwoStage({'updates': [proposed], 'retire': []}, check)
        maintainer = Maintainer(self.store, self.pack, provider, attribution_check=True)
        self.assertFalse(await maintainer.once())
        self.assertEqual(maintainer.error, 'ValueError')
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(self.snapshot(), before)
        self.store.close(); self.store = self.open()
        self.assertEqual(self.snapshot(), before)
