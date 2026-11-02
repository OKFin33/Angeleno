"""Private synthetic trace contracts; fake providers/HTTP, never user data or services."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from her_agent import trace
from her_agent.runtime import ConversationRuntime, open_store
from her_agent.provider import TextDelta
from her_agent.web_lookup import WebLookup, DeepSeekWeb
from test_lookup import PACK, CLOCK, Scripted, call
from test_web_lookup import FakeWeb
from test_provider import offline_provider, response, event, DONE, USAGE
from test_budget import budget_env, mocked_providers, balance_response


def env_at(root,purpose='development',**changes):
    (root/'private').mkdir(exist_ok=True)
    return {'ANGELENO_TRACE':'1','ANGELENO_MEMORY_PURPOSE':purpose,
            'ANGELENO_DIAGNOSTICS_FILE':str(root/'private/operator.jsonl'),**changes}


def rows_at(env,root,purpose='development'):
    return trace.load_rows(Path(env['ANGELENO_DIAGNOSTICS_FILE']),purpose,root=root)


class SinkTests(unittest.TestCase):
    def test_default_off_and_safe_path_mode_purpose_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=env_at(root)
            disabled=trace.Trace({},root=root,purpose='development')
            self.assertFalse(trace.record('test',binding=trace.Binding(disabled,'s'),text='not saved'))
            target=Path(env['ANGELENO_DIAGNOSTICS_FILE']);self.assertFalse(target.exists())
            sink=trace.Trace(env,root=root,purpose='development');binding=trace.Binding(sink,'s','t1')
            self.assertTrue(trace.record('turn_input',binding=binding,text='public synthetic'))
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            other=trace.Trace({**env,'ANGELENO_MEMORY_PURPOSE':'real_user'},root=root,purpose='real_user')
            self.assertFalse(trace.record('test',binding=trace.Binding(other,'other'),text='REAL_CANARY'))
            with self.assertRaises(ValueError):rows_at(env,root,'real_user')
            self.assertNotIn('REAL_CANARY',target.read_text())
            target.chmod(0o644);self.assertFalse(trace.record('test',binding=binding));target.chmod(0o600)
            with patch.object(trace,'MAX_EVENT_BYTES',500):
                trace.record('large',binding=binding,text='large public fixture'*200)
            self.assertTrue(rows_at(env,root)[-1]['data']['capture_truncated'])
            with patch.object(trace,'MAX_FILE_BYTES',target.stat().st_size):
                trace.record('overflow',binding=binding,text='not retained')
                self.assertEqual(json.loads(target.read_text().splitlines()[-1])['event'],'capture_limit')
            link=root/'private/link.jsonl';link.symlink_to(target)
            self.assertIsNone(trace.Trace({**env,'ANGELENO_DIAGNOSTICS_FILE':str(link)},root=root,purpose='development').path)
            self.assertIsNone(trace.Trace({**env,'ANGELENO_DIAGNOSTICS_FILE':str(root/'outside.jsonl')},root=root,purpose='development').path)

    def test_credentials_headers_audio_and_temporary_content_never_enter_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=env_at(root,DEEPSEEK_API_KEY='exact-synthetic-secret-value')
            sink=trace.Trace(env,root=root,purpose='development');binding=trace.Binding(sink,'s','t1')
            trace.record('data',binding=binding,messages=[{'content':'exact-synthetic-secret-value sk-abcdefghijk'}],
                         authorization='Bearer RAW_BEARER',cookie='RAW_COOKIE',audio=b'RAW_AUDIO',
                         text='Authorization: Bearer HIDDEN\nCookie: SESSION=HIDDEN\napi_key=ANOTHER_SECRET\nmy password is NATURAL_SECRET\n-----BEGIN PRIVATE KEY-----PEM_CANARY-----END PRIVATE KEY-----')
            trace.record('turn_input',binding=trace.Binding(sink,'s','t2',False),text='TEMPORARY_CANARY')
            text=Path(env['ANGELENO_DIAGNOSTICS_FILE']).read_text()
            for bad in ['exact-synthetic-secret-value','sk-abcdefghijk','RAW_BEARER','RAW_COOKIE','RAW_AUDIO','HIDDEN','ANOTHER_SECRET','NATURAL_SECRET','PEM_CANARY','TEMPORARY_CANARY']:
                self.assertNotIn(bad,text)
            self.assertEqual(rows_at(env,root)[-1]['data'],{'content_omitted':'temporary_no_save'})
            self.assertTrue(rows_at(env,root)[0]['capture_redacted'])


class TraceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def plain(self,root,enabled=True,temporary=False,fail_write=False):
        env=env_at(root,ANGELENO_TRACE='1' if enabled else '0')
        requests=[]
        def handler(req):requests.append(json.loads(req.content));return response('VISIBLE_REPLY')
        provider=offline_provider(handler)
        runtime=ConversationRuntime(PACK,provider,language='en',trace_env=env,trace_root=root)
        if temporary:runtime.set_temporary(True)
        try:
            turn=runtime.begin('TEMP_INPUT' if temporary else 'Public synthetic chat.')
            if fail_write:
                with patch('her_agent.trace.os.open',side_effect=PermissionError):
                    text=''.join([p async for p in runtime.stream(turn)])
            else:text=''.join([p async for p in runtime.stream(turn)])
            runtime.delivered(turn,text,complete=True)
            return env,requests,text
        finally:await runtime.close(flush=False)

    async def test_on_off_same_payload_output_timings_usage_and_viewer_fold(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            off,request0,text0=await self.plain(Path(a),False)
            on,request1,text1=await self.plain(Path(b),True)
            self.assertEqual(request0,request1);self.assertEqual(text0,text1)
            self.assertFalse(Path(off['ANGELENO_DIAGNOSTICS_FILE']).exists())
            rows=rows_at(on,Path(b));request=next(x['data'] for x in rows if x['event']=='model_request')
            self.assertEqual(request['payload'],request1[0])
            end=next(x['data'] for x in rows if x['event']=='model_end')
            self.assertEqual(end['usage'],USAGE);self.assertEqual(end['finish_reason'],'stop')
            self.assertGreaterEqual(end['elapsed_seconds'],end['first_chunk_seconds'])
            self.assertEqual(end['proposal'],'none');self.assertFalse(end['tools_offered'])
            view=trace.report(rows)[0]['turns'][0];self.assertEqual(view['delivery']['text'],'VISIBLE_REPLY')
            self.assertNotIn('details',view)
            expanded=trace.report(rows,expand=['input'])[0]['turns'][0]
            self.assertTrue(any(x['event']=='model_request' for x in expanded['details']))

    async def test_write_failure_is_nonfatal_and_temporary_has_no_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env,_,text=await self.plain(root,fail_write=True)
            self.assertEqual(text,'VISIBLE_REPLY')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env,_,text=await self.plain(root,temporary=True)
            raw=Path(env['ANGELENO_DIAGNOSTICS_FILE']).read_text()
            self.assertNotIn('TEMP_INPUT',raw);self.assertNotIn('VISIBLE_REPLY',raw)

    async def test_supplier_reasoning_only_opt_in_without_enabling_thinking(self):
        body='data: '+json.dumps({'choices':[{'delta':{'reasoning_content':'SUPPLIER_RETURNED_TEST'},'finish_reason':None}]})+'\n\n'+event('reply')+event(reason='stop',usage=USAGE)+DONE
        for flag in ('0','1'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);env=env_at(root,ANGELENO_TRACE_REASONING=flag)
                provider=offline_provider(lambda r:httpx.Response(200,text=body))
                sink=trace.Trace(env,root=root,purpose='development')
                try:
                    with trace.activate(trace.Binding(sink,'s','t1')):
                        self.assertEqual(''.join([p async for p in provider.chat([{'role':'user','content':'public'}])]),'reply')
                    text=Path(env['ANGELENO_DIAGNOSTICS_FILE']).read_text()
                    self.assertEqual('SUPPLIER_RETURNED_TEST' in text,flag=='1')
                    self.assertEqual(provider.budget.attempts[0][0]['thinking'],{'type':'disabled'})
                finally:await provider.aclose()

    async def test_tool_parameters_evidence_and_real_user_gate_are_observable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=env_at(root,'real_user')
            actor=Scripted();actor.rounds=[[call('web_search',{'area':'usc_exposition','topic':'architecture'})],
                [call('web_fetch',{'result_id':'rq-2-0'},call_id='fetch')],[TextDelta('source-aware synthetic reply')]]
            runtime=ConversationRuntime(PACK,actor,lookup=WebLookup(FakeWeb(),CLOCK),trace_env=env,trace_root=root)
            try:
                turn=runtime.begin('Public synthetic architecture request.')
                text=''.join([p async for p in runtime.stream(turn)]);runtime.delivered(turn,text,complete=True)
                rows=rows_at(env,root,'real_user')
                proposals=[r['data'] for r in rows if r['event']=='tool_proposed']
                self.assertEqual(len(proposals),2);self.assertIn('usc_exposition',proposals[0]['arguments'])
                evidence=[r['data']['result'] for r in rows if r['event']=='tool_evidence']
                self.assertEqual(evidence[0]['sources'][0]['claim_kind'],'search_summary')
                self.assertEqual(evidence[1]['sources'][0]['claim_kind'],'fetched_page')
                self.assertFalse(any(r['event']=='web_http_start' for r in rows),'Local adapter is not HTTP proof')
            finally:await runtime.close(flush=False)

    async def test_cancelled_generation_and_redial_never_share_turn_context(self):
        class Slow:
            async def chat(self,messages,**kw):
                yield 'partial';await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=env_at(root)
            first=ConversationRuntime(PACK,Slow(),trace_env=env,trace_root=root)
            turn=first.begin('public one');started=asyncio.Event()
            async def collect():
                async for _ in first.stream(turn):started.set()
            task=asyncio.create_task(collect());await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            first.delivered(turn,'partial',complete=False);sid=first.trace_session;await first.close(flush=False)
            second=ConversationRuntime(PACK,Slow(),trace_env=env,trace_root=root)
            self.assertNotEqual(sid,second.trace_session)
            trace.record('timing',binding=turn.trace_binding,kind='late_old_event')
            await second.close(flush=False)
            rows=rows_at(env,root);old=[r for r in rows if r['session']==sid]
            self.assertEqual(next(r['data']['status'] for r in old if r['event']=='generation_end'),'cancelled')
            self.assertFalse(next(r['data']['complete'] for r in old if r['event']=='delivery'))
            self.assertFalse(any(r['event']=='timing' for r in rows if r['session']==second.trace_session))

    async def test_actual_HTTP_boundary_has_separate_search_receipt_and_ledger_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=env_at(root);providers=mocked_providers(budget_env(root),lambda req:balance_response() if req.method=='GET' else httpx.Response(200,json={'results':[]}))
            sink=trace.Trace(env,root=root,purpose='development')
            try:
                from her_agent.web_lookup import PublicQuery
                with trace.activate(trace.Binding(sink,'s','t1')):
                    await DeepSeekWeb(providers[0],request_limit='account').search(PublicQuery('usc_exposition','architecture'))
                rows=rows_at(env,root);http=[r['data'] for r in rows if r['event'].startswith('web_http_')]
                self.assertEqual(http[0]['operation'],'search');self.assertEqual(http[1]['http_status'],200)
                self.assertEqual(http[0]['ledger_request_id'],http[1]['ledger_request_id'])
            finally:await asyncio.gather(*(p.aclose() for p in providers))


class TraceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reservation_refused_or_HTTP_timeout_has_terminal_record_no_retry(self):
        from her_agent.budget import BudgetExceeded
        for kind in ('budget','timeout'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);env=env_at(root)
                def handler(req):raise httpx.ReadTimeout('do not record raw error headers')
                provider=offline_provider(handler)
                if kind=='budget':provider.budget.reserve=__import__('unittest.mock',fromlist=['AsyncMock']).AsyncMock(side_effect=BudgetExceeded('private detail'))
                sink=trace.Trace(env,root=root,purpose='development')
                try:
                    with trace.activate(trace.Binding(sink,'s','t1')):
                        with self.assertRaises((BudgetExceeded,httpx.ReadTimeout)):
                            _=[p async for p in provider.chat([{'role':'user','content':'public'}])]
                    rows=rows_at(env,root);end=next(r['data'] for r in rows if r['event']=='model_end')
                    self.assertEqual(end['status'],'reservation_refused' if kind=='budget' else 'failed')
                    self.assertEqual(sum(r['event']=='model_http_start' for r in rows),0 if kind=='budget' else 1)
                    self.assertNotIn('private detail',Path(env['ANGELENO_DIAGNOSTICS_FILE']).read_text())
                finally:await provider.aclose()

    async def test_memory_commit_is_linked_to_batch_and_not_an_actor_claim(self):
        class Plain:
            async def chat(self,messages,**kw):yield 'public reply'
        class Writer:
            async def chat(self,messages,**kw):yield '{"retire":[],"updates":[]}'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=env_at(root);store=open_store(root,PACK,'dev-trace',create=True,purpose='development')
            runtime=ConversationRuntime(PACK,Plain(),store=store,writer=Writer(),trace_env=env,trace_root=root)
            try:
                for n in range(3):
                    turn=runtime.begin('public topic '+str(n));text=''.join([p async for p in runtime.stream(turn)])
                    runtime.delivered(turn,text,complete=True)
                await runtime.maintainer.task
                rows=rows_at(env,root);commit=next(r for r in rows if r['event']=='memory_commit')
                self.assertEqual(commit['turn'],'t3');self.assertEqual(commit['data']['watermark'],store.watermark)
                self.assertEqual(store.pending(),[])
                self.assertTrue(any(r['event']=='memory_state' for r in rows))
            finally:await runtime.close(flush=False)

    async def test_card_send_and_timing_bind_to_correct_turn_without_audio_capture(self):
        from unittest.mock import Mock,AsyncMock
        from her_agent.voice_cards import VoiceCards
        from her_agent import diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);env=env_at(root)
            service=WebLookup(FakeWeb(),CLOCK);actor=Scripted()
            actor.rounds=[[call('web_search',{'area':'usc_exposition','topic':'architecture'})],[TextDelta('public reply')]]
            runtime=ConversationRuntime(PACK,actor,lookup=service,trace_env=env,trace_root=root)
            peer=Mock();peer.publish_data=AsyncMock();sender=VoiceCards(service,peer,'synthetic-recipient')
            try:
                turn=runtime.begin('public source request');text=''.join([p async for p in runtime.stream(turn)])
                runtime.delivered(turn,text,complete=True)
                for _ in range(10):await asyncio.sleep(0)
                trace.register('her_current',turn.trace_binding)
                with patch.dict('os.environ',env),patch.object(diagnostics,'ROOT',root):
                    diagnostics.emit('playout_finished',request_id='her_current',phase='answer',interrupted=False)
                rows=rows_at(env,root)
                sent=[r for r in rows if r['event']=='card_sent'];self.assertTrue(sent)
                self.assertTrue(all(r['turn']=='t1' for r in sent));self.assertEqual(sent[-1]['data']['card']['citations'][0]['claim_kind'],'search_summary')
                self.assertTrue(trace.report(rows)[0]['turns'][0]['cards'])
                self.assertTrue(any(r['event']=='card_sent' for r in trace.report(rows,expand=['tools'])[0]['turns'][0]['details']))
                timing=[r for r in rows if r['event']=='timing'];self.assertEqual(timing[-1]['turn'],'t1')
                self.assertEqual(timing[-1]['data']['kind'],'playout_finished')
            finally:await sender.close();await runtime.close(flush=False)
