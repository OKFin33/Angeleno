"""Fake official HTTP and synthetic material only; no private Jo or real model."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import httpx
from unittest.mock import patch

from angeleno.author import assistant_control_pack, load_pack
from angeleno.comparison import digest, file_hash, load_config
from angeleno.comparison_live import ROOT, OperatorBranches, Receipt, load_manifest, main, revision, run_models
from test_budget import budget_env, balance_response, mocked_providers
from test_provider import response, event

CONFIG=ROOT/'examples/comparison/development.json'
PACK_PATH=ROOT/'examples/demo/author-pack.json'
PACK=load_pack(PACK_PATH)


class RealEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT/'tmp').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tmp')
        self.root=Path(self.tmp.name);self.env=budget_env(self.root)
        self.c,self.anchors,self.corpus=load_config(CONFIG)
        self.http=[];self.fail=False;self.block=None
        def handle(request):
            self.http.append(request)
            if request.method=='GET': return balance_response()
            if self.fail: return response(usage=None)
            payload=json.loads(request.content)
            return response('{"retire":[],"updates":[]}' if payload.get('response_format') else 'Synthetic whole answer.')
        self.handle=handle
        pair=mocked_providers(self.env,handle)
        self.ledger=pair[0].budget.ledger
        await asyncio.gather(*(p.aclose() for p in pair))
        self.m={'format':'angeleno-development-models/1','status':'frozen_development_inputs',
            'holdout_included':False,'branch_policy':'stop_unclassified','candidate_commit':revision(),
            'config_sha256':file_hash(CONFIG),'authorization_reference':'synthetic tests only',
            'corpus_acceptance':'synthetic mechanism; not preference evidence',
            'model_version_limitation':'alias only in this fake test','model_alias':self.c['model'],
            'runtime_root':str(self.root),'ledger_path':str(self.ledger.path),
            'character_pack':{'path':str(PACK_PATH),'sha256':file_hash(PACK_PATH)},'shared_grounding':[],
            'roles_sha256':{'character':digest(PACK.provenance()),'assistant':digest(assistant_control_pack(PACK).provenance())}}
        self.out=self.root/'receipt.json'

    async def asyncTearDown(self): self.tmp.cleanup()

    async def run_entry(self, **kw):
        return await run_models(self.c,self.anchors,self.corpus,PACK,self.env,self.m,output=self.out,
            provider_builder=lambda:mocked_providers(self.env,self.handle),**kw)

    async def test_original_ledger_each_request_and_same_core_complete_six_segments(self):
        with patch('socket.socket.connect',side_effect=AssertionError('no real network')),patch('socket.getaddrinfo',side_effect=AssertionError('no DNS')):
            r=await self.run_entry(branch_selector=lambda *args:None)
        self.assertEqual(r['status'],'completed');self.assertEqual(len(r['segments']),6)
        self.assertEqual(r['external_web_requests'],0);self.assertIsNone(r['owner_calibration'])
        rows=self.ledger.summary()['rows']
        self.assertEqual(len(rows),len(r['requests']))
        self.assertTrue(any(x['purpose']=='maintenance' for x in rows))
        self.assertTrue(all(x['output'] for x in r['requests']))
        self.assertTrue(all(json.loads(x['output'])=={'retire':[],'updates':[]} for x in r['requests'] if x['kind']=='writer'))
        self.assertEqual([x.method for x in self.http],['GET','POST']*len(rows))
        self.assertTrue(all(x.url.host=='api.deepseek.com' for x in self.http))
        self.assertTrue(all(s['pending_events']==0 for s in r['segments']))
        self.assertTrue(all(s['state_disposition']=='removed_after_close' for s in r['segments']))
        self.assertEqual(json.loads(self.out.read_text())['status'],'completed')
        self.assertEqual(self.out.stat().st_mode & 0o777,0o600)
        self.assertFalse(self.out.with_name('receipt.json.partial').exists())
        self.assertEqual({t['user_created'] for s in r['segments'] for t in s['transcript']},{self.c['clock_utc']})

    async def test_unknown_branch_stops_whole_batch_and_leaves_first_output(self):
        r=await self.run_entry()
        self.assertEqual(r['status'],'incomplete');self.assertEqual(len(r['segments']),1)
        self.assertEqual(r['segments'][0]['reason'],'branch_uncovered')
        self.assertEqual(len(self.ledger.summary()['rows']),1)
        self.assertEqual(r['segments'][0]['transcript'][0]['delivery'],'complete')

    async def test_missing_usage_stops_without_other_arm_and_retains_unknown_accounting(self):
        self.fail=True
        r=await self.run_entry(branch_selector=lambda *args:None)
        self.assertEqual(len(r['segments']),1);self.assertEqual(r['status'],'incomplete')
        self.assertEqual(len(r['requests']),1);self.assertEqual(r['requests'][0]['status'],'failed_or_cancelled')
        self.assertGreater(r['ledger_current']['unresolved_reserved_micros'],0)
        self.assertEqual(r['segments'][0]['transcript'][0]['delivery'],'interrupted')

    def historical_web_row(self):
        ident = self.ledger.reserve_web('web_fetch', 100000000, 0, 6)
        self.ledger.finish_web(ident, charged=None, status='cancelled')
        return self.ledger.summary()['rows'][-1]

    async def test_historical_unknown_default_still_refuses_before_http(self):
        self.historical_web_row()
        with self.assertRaisesRegex(Exception, 'existing_unresolved_usage'):
            await self.run_entry(branch_selector=lambda *args:None)
        self.assertEqual(self.http, [])

    async def test_authorized_exact_historical_unknown_remains_reserved_through_six_segments(self):
        row = self.historical_web_row()
        self.m.update(historical_unresolved=[row], historical_unresolved_authorization='  Synthetic explicit continuation approval  ')
        r = await self.run_entry(branch_selector=lambda *args:None)
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['ledger_current']['rows'][0], row)
        self.assertEqual(r['ledger_current']['unresolved_reserved_micros'], 5000)
        self.assertEqual(r['historical_unresolved'], [row])

    async def test_historical_authorization_reference_requires_nonblank_string_before_payload_or_http(self):
        row = self.historical_web_row()
        path = self.root/'manifest.json'
        for reference in (None, False, {}, [], '', ' \t\n'):
            with self.subTest(reference=reference):
                self.m.update(historical_unresolved=[row], historical_unresolved_authorization=reference)
                path.write_text(json.dumps(self.m))
                with patch('angeleno.comparison_live.load_pack', side_effect=AssertionError('no payload load')):
                    with self.assertRaisesRegex(ValueError, 'authorization reference'):
                        load_manifest(path, CONFIG)
                with self.assertRaisesRegex(ValueError, 'authorization reference'):
                    await self.run_entry(branch_selector=lambda *args:None)
                self.assertEqual(self.http, [])
                self.assertFalse(self.out.exists())
                self.assertEqual(self.ledger.summary()['rows'], [row])

    async def test_new_unknown_stops_even_with_authorized_historical_reserve(self):
        row = self.historical_web_row()
        self.m.update(historical_unresolved=[row], historical_unresolved_authorization='Synthetic explicit continuation approval')
        self.fail = True
        r = await self.run_entry(branch_selector=lambda *args:None)
        self.assertEqual(r['status'], 'incomplete')
        self.assertEqual(len(r['requests']), 1)
        self.assertEqual(len(r['segments']), 1)
        self.assertEqual(r['ledger_current']['rows'][0], row)
        self.assertGreater(r['ledger_current']['unresolved_reserved_micros'], 5000)

    async def test_historical_unknown_drift_pending_or_missing_authority_refuses(self):
        row = self.historical_web_row()
        for rows, reference in (([row], ''), ([{**row, 'status':'reserved'}], 'explicit'),
                                ([{**row, 'reserved_micros':1}], 'explicit')):
            self.m.update(historical_unresolved=rows, historical_unresolved_authorization=reference)
            self.out=self.out.with_name(self.out.stem+'-next.json')
            with self.assertRaises(Exception):await self.run_entry(branch_selector=lambda *args:None)
        self.assertEqual(self.http, [])

    async def test_cancelled_real_stream_persists_prefix_unknown_cost_and_stops(self):
        ready=asyncio.Event()
        class Hanging(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield event('Synthetic partial prefix').encode()
                ready.set();await asyncio.Future()
        self.handle=lambda req: balance_response() if req.method=='GET' else httpx.Response(200,stream=Hanging())
        task=asyncio.create_task(self.run_entry(branch_selector=lambda *args:None))
        await ready.wait();task.cancel();r=await task
        self.assertTrue(r['cancelled']);self.assertEqual(len(r['segments']),1)
        self.assertEqual(r['segments'][0]['transcript'][0]['output'],'Synthetic partial prefix')
        self.assertEqual(r['segments'][0]['transcript'][0]['delivery'],'interrupted')
        saved=json.loads(self.out.read_text())
        self.assertEqual(saved['status'],'incomplete')
        self.assertGreater(saved['ledger_current']['unresolved_reserved_micros'],0)
        self.assertEqual(len(self.ledger.summary()['rows']),1)

    async def test_low_balance_stops_before_model_post_and_other_arm(self):
        def handle(req):
            self.http.append(req)
            return balance_response('0')
        self.handle=handle
        r=await self.run_entry(branch_selector=lambda *args:None)
        self.assertEqual(r['status'],'incomplete');self.assertEqual(len(r['segments']),1)
        self.assertEqual([x.method for x in self.http],['GET'])
        self.assertEqual(self.ledger.summary()['rows'],[])

    async def test_writer_failure_is_a_barrier_before_next_turn_and_arm(self):
        def handle(req):
            self.http.append(req)
            return balance_response() if req.method=='GET' else response('not maintenance JSON' if json.loads(req.content).get('response_format') else 'Synthetic answer')
        self.handle=handle
        r=await self.run_entry(branch_selector=lambda *args:None)
        self.assertEqual(len(r['segments']),1)
        self.assertEqual(len(r['segments'][0]['transcript']),3)
        self.assertEqual(r['segments'][0]['reason'],'maintenance_JSONDecodeError')
        self.assertEqual([x['purpose'] for x in self.ledger.summary()['rows']],['actor']*3+['maintenance'])

    async def test_unobserved_query_is_not_an_empty_result(self):
        from angeleno.comparison import CorpusWeb, RunStopped
        from angeleno.web_lookup import PublicQuery
        corpus=deepcopy(self.corpus)
        for row in corpus['queries']:
            row['record_ids']=[];row['uncovered']=True
        web=CorpusWeb(corpus,'normal')
        with self.assertRaises(RunStopped): await web.search(PublicQuery('usc_exposition','architecture'))
        self.assertEqual(web.calls,[])

    async def test_operator_routes_six_segments_and_copies_dynamic_reply_from_same_arm(self):
        prompts=[]
        def emit(text, **kw): prompts.append(json.loads(text))
        def read():
            prompt=prompts[-1]
            choice='2' if prompt['anchor']=='DEV-D1' and prompt['turn']==1 else 'continue'
            command={'decision':choice,'output_sha256':prompt['output_sha256']}
            if choice=='2': command.update(span=[0,6],rule='first_concrete_place_v1')
            return json.dumps(command)
        original=self.handle
        def handle(req):
            if req.method=='POST' and not json.loads(req.content).get('response_format'):
                self.http.append(req);return response('Fisher更有意思。也可以看NHM。')
            return original(req)
        self.handle=handle;self.m['branch_policy']='operator_v1'
        r=await self.run_entry(branch_selector=OperatorBranches(read,emit))
        self.assertEqual(r['status'],'completed');self.assertEqual(len(r['segments']),6)
        for s in [s for s in r['segments'] if s['anchor']=='DEV-D1']:
            self.assertEqual(s['transcript'][1]['user'],'Fisher')
            self.assertEqual(s['transcript'][0]['branch_reply'],'Fisher')
            self.assertEqual(s['transcript'][0]['branch_decision']['span'],[0,6])

    async def test_operator_unknown_wrong_hash_or_rewritten_span_stops_before_next_arm(self):
        for command in ({'decision':'uncovered'}, {'decision':'continue','output_sha256':'wrong'},
                        {'decision':'2','span':[0,9999],'rule':'first_concrete_place_v1'}):
            prompts=[]
            def emit(text, **kw): prompts.append(json.loads(text))
            def read(): return json.dumps({'output_sha256':prompts[-1]['output_sha256'],**command})
            if self.out.exists(): self.out=self.out.with_name(self.out.stem+'-next.json')
            r=await self.run_entry(branch_selector=OperatorBranches(read,emit))
            self.assertEqual(len(r['segments']),1)
            self.assertEqual(r['segments'][0]['reason'],'branch_uncovered')
            self.assertEqual(len(r['segments'][0]['transcript']),1)

    async def test_static_operator_branch_uses_only_listed_reply_once(self):
        prompts=[]
        def emit(text, **kw): prompts.append(json.loads(text))
        def read():
            p=prompts[-1]
            return json.dumps({'output_sha256':p['output_sha256'],
                'decision':'1' if p['anchor']=='DEV-D1' else 'continue'})
        r=await self.run_entry(branch_selector=OperatorBranches(read,emit))
        self.assertEqual(r['status'],'incomplete');self.assertEqual(len(r['segments']),1)
        rows=r['segments'][0]['transcript']
        self.assertEqual(rows[1]['user'],self.anchors[0]['clarifications'][0]['reply'])
        self.assertEqual(r['segments'][0]['reason'],'branch_uncovered')

    async def test_hold_and_operator_flag_cannot_silently_open_real_mode(self):
        p=self.root/'held.json';held={**self.m,'execution_hold':True};p.write_text(json.dumps(held))
        with self.assertRaises(ValueError): load_manifest(p,CONFIG)
        self.m['execution_hold']=True
        with self.assertRaises(ValueError): await self.run_entry()
        self.m.pop('execution_hold')
        self.m['branch_policy']='operator_v1'
        with self.assertRaises(ValueError): await self.run_entry()
        self.assertEqual(self.http,[])

    async def test_existing_ledger_and_same_output_caps_are_required_before_http(self):
        self.m['ledger_path']=str(self.root/'new.sqlite3')
        with self.assertRaises(ValueError): await self.run_entry()
        self.assertEqual(self.http,[])
        self.assertFalse((self.root/'new.sqlite3').exists())
        self.m['ledger_path']=str(self.ledger.path);self.c['actor_max_tokens']=1024
        with self.assertRaises(ValueError): await self.run_entry()
        self.assertEqual(self.http,[])
        self.assertEqual(json.loads(self.out.read_text())['status'],'aborted')

    async def test_default_cli_does_not_read_env_or_construct_paid_provider(self):
        with patch('angeleno.comparison_live.load_env',side_effect=AssertionError('no config')),patch('angeleno.comparison_live.configured_providers',side_effect=AssertionError('no provider')):
            result=await main(['--output',str(self.out)])
        self.assertEqual(result,0)
        self.assertEqual(json.loads(self.out.read_text())['mode'],'dry_run')
        self.assertEqual(self.http,[])

    async def test_manifest_rejects_drift_outcomes_and_wrong_candidate(self):
        p=self.root/'manifest.json';p.write_text(json.dumps(self.m))
        m,pack=load_manifest(p,CONFIG)
        self.assertEqual(pack,PACK)
        for field,value in [('candidate_commit','bad'),('holdout_included',True),('roles_sha256',{}),('config_sha256','bad'),('authorization_reference','')]:
            bad=deepcopy(self.m);bad[field]=value;p.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):load_manifest(p,CONFIG)
        bad=deepcopy(self.m);bad['character_pack']['sha256']='bad';p.write_text(json.dumps(bad))
        with self.assertRaises(ValueError):load_manifest(p,CONFIG)

    async def test_receipt_is_new_only_and_does_not_follow_symlink(self):
        self.out.write_text('KEEP')
        with self.assertRaises(FileExistsError): Receipt(self.out)
        self.assertEqual(self.out.read_text(),'KEEP')
        link=self.root/'link.json';link.symlink_to(self.out)
        with self.assertRaises(ValueError): Receipt(link)
        self.assertEqual(self.out.read_text(),'KEEP')
