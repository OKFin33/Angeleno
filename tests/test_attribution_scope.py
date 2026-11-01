"""Synthetic scope guard examples; fake judgments are not model reliability."""
from copy import deepcopy
import json
import unittest

from her_agent.attribution import AttributionRejected, validate_check
from her_agent.continuity import Maintainer
from test_memory import StoreFixture, record
from test_attribution_candidate import TwoStage


def fixture(claim, user_text, *, scope='user_claims', verdict='supported', role='user', quote=None):
    payload={'candidates':[{'id':'card.user' if scope=='user_claims' else 'episode.role-past',
              'kind':'user' if scope=='user_claims' else 'episode','action':'update',
              'digest':'a'*64,'clauses':[claim],'keywords':[]}],
             'sources':[{'seq':1,'role':role,'text':user_text,'created':'2026-10-30T22:04:05+00:00','new_in_batch':True}]}
    result={'checks':[{'id':payload['candidates'][0]['id'],'digest':'a'*64,'scope':scope,'verdict':verdict,
                       'evidence':[{'seq':1,'quote':quote or user_text}],
                       'issue':None if verdict=='supported' else {'quote':claim,'reason':'synthetic conflict'}}]}
    return payload,result


class RefusalScopeTests(unittest.TestCase):
    def test_singular_citation_cannot_certify_plural_refusal(self):
        payload,result=fixture('用户拒绝这两个选项。','这个我不太喜欢，能说说为什么选它？')
        original=deepcopy(result)
        with self.assertRaisesRegex(AttributionRejected,'scope unresolved'):
            validate_check(result,payload)
        self.assertEqual(result,original,'Keep actual model judgment unchanged, reject before memory apply')

    def test_explicit_plural_and_complete_source_with_shortened_quote_can_pass(self):
        for utterance,claim in [('两个选项我都不喜欢。','用户明确拒绝这两个选项。'),
                                 ('我明确拒绝这两个方向。','用户明确拒绝这两个方向。'),
                                 ('这三家店都不考虑。','用户排除这三家店。')]:
            with self.subTest(utterance=utterance):
                payload,result=fixture(claim,utterance,quote='不喜欢' if '不喜欢' in utterance else None)
                validate_check(result,payload)

    def test_singular_refusal_and_role_proposals_are_not_blanket_blocked(self):
        for claim in ['用户说这个不太喜欢，所指尚未确定。',
                      '助手提出两个选项。用户说这个不太喜欢，所指尚未确定。',
                      '用户未明确拒绝这两个选项，只说这个不太喜欢。',
                      '用户问这两个选项为什么不喜欢。']:
            payload,result=fixture(claim,'这个我不太喜欢。')
            validate_check(result,payload)

    def test_other_quantified_objects_and_assistant_scope_do_not_supply_user_scope(self):
        payload,result=fixture('用户排除这三家店。','那家我不考虑。')
        payload['sources'].append({'seq':2,'role':'assistant','text':'你是说三家都不考虑吗？','new_in_batch':True})
        result['checks'][0]['evidence'].append({'seq':2,'quote':'你是说三家都不考虑吗？'})
        with self.assertRaises(AttributionRejected):validate_check(result,payload)

    def test_negated_or_questioned_plural_is_not_explicit_confirmation(self):
        for utterance in ['不是两个都不喜欢，只是这个不太喜欢。',
                          '这个不太喜欢，是两个都不喜欢的意思吗？']:
            payload,result=fixture('用户两个选项都不喜欢。',utterance)
            with self.subTest(utterance=utterance),self.assertRaises(AttributionRejected):
                validate_check(result,payload)

    def test_conflict_and_no_user_claims_role_past_keep_original_contract(self):
        payload,result=fixture('用户不喜欢两个选项。','我两个都喜欢。',verdict='conflict')
        with self.assertRaises(AttributionRejected):validate_check(result,payload)
        payload,result=fixture('角色说上次看展只看了一半。','我上次看展只看了一半。',scope='no_user_claims',role='assistant')
        validate_check(result,payload)


class RefusalScopeCommitTests(StoreFixture,unittest.IsolatedAsyncioTestCase):
    async def test_wrong_supported_scope_keeps_old_state_and_raw_backlog(self):
        old=self.exchange('我喜欢安静。')
        self.apply(record('card.user','用户喜欢安静。',[old[0]],kind='user'))
        fresh=self.exchange('这个我不太喜欢。','你是说两个方向都不喜欢吗？')
        proposed=record('card.user','用户不喜欢这两个方向。',[fresh[0]],kind='user')
        def supported(payload):
            source=next(s for s in payload['sources'] if s['seq']==fresh[0])
            return {'checks':[{'id':c['id'],'digest':c['digest'],'scope':'user_claims','verdict':'supported',
                              'evidence':[{'seq':source['seq'],'quote':source['text']}],'issue':None}
                             for c in payload['candidates']]}
        provider=TwoStage({'updates':[proposed],'retire':[]},supported)
        maintainer=Maintainer(self.store,self.pack,provider,attribution_check=True)
        before='\n'.join(self.store.db.iterdump())
        self.assertFalse(await maintainer.once());self.assertEqual(maintainer.error,'AttributionRejected')
        self.assertEqual(maintainer.last_attempt['attribution_result']['checks'][0]['verdict'],'supported')
        self.assertEqual('\n'.join(self.store.db.iterdump()),before)
        maintainer.schedule();await maintainer.flush();self.assertEqual(len(provider.requests),2)
        self.store.close();self.store=self.open();self.assertEqual('\n'.join(self.store.db.iterdump()),before)
        clear=self.store.append('synthetic-current','user','这两个方向我都不喜欢。')
        corrected=record('card.user','用户不喜欢这两个方向。',[clear],kind='user')
        resumed=Maintainer(self.store,self.pack,TwoStage({'updates':[corrected],'retire':[]}),attribution_check=True)
        self.assertTrue(await resumed.once());self.assertEqual(self.store.pending(),[])
        self.assertEqual(self.store.records()[0]['text'],corrected['text'])


class AttributionRuntimeOptionTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    async def test_voice_runtime_opt_in_and_default_use_existing_submit_gate(self):
        from her_agent.voice import create_voice_runtime
        for mode in (None, '1'):
            actor = TwoStage({'updates': [], 'retire': []})
            writer = TwoStage({'updates': [], 'retire': []})
            env = {'ANGELENO_AUTHOR_PACK': str(self.root / 'pack.json'),
                   'ANGELENO_MEMORY_USER': 'runtime-' + ('on' if mode else 'off'),
                   'ANGELENO_MEMORY_CREATE': '1'}
            if mode is not None:env['ANGELENO_MEMORY_ATTRIBUTION_CHECK'] = mode
            runtime = create_voice_runtime(env, root=self.root, provider_factory=lambda _: (actor, writer))
            try:
                self.assertEqual(runtime.maintainer.attribution_check, mode == '1')
                seq = runtime.store.append('synthetic-runtime', 'user', '我喜欢安静。')
                writer.proposal = {'updates': [record('card.user', '用户喜欢安静。', [seq], kind='user')], 'retire': []}
                self.assertTrue(await runtime.maintainer.once())
                self.assertEqual(len(writer.requests), 2 if mode else 1)
            finally:await runtime.close(flush=False)

    async def test_invalid_option_refuses_before_provider_or_store_creation(self):
        from her_agent.voice import create_voice_runtime
        env = {'ANGELENO_AUTHOR_PACK': str(self.root / 'pack.json'), 'ANGELENO_MEMORY_USER': 'invalid',
               'ANGELENO_MEMORY_CREATE': '1', 'ANGELENO_MEMORY_ATTRIBUTION_CHECK': 'yes'}
        with self.assertRaisesRegex(ValueError, 'must be 0 or 1'):
            create_voice_runtime(env, root=self.root, provider_factory=lambda _: self.fail('No provider'))
        self.assertFalse((self.root / 'private/state').exists())
