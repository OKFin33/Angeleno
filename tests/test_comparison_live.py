"""Fake official HTTP and synthetic material only; no private Jo or real model."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import httpx
from unittest.mock import patch

from her_agent.author import assistant_control_pack, load_pack
from her_agent.comparison import digest, file_hash, load_config
from her_agent.comparison_live import ROOT, Receipt, load_manifest, main, revision, run_models
from test_budget import budget_env, balance_response, mocked_providers
from test_provider import response, event

CONFIG=ROOT/'examples/comparison/development.json'
PACK_PATH=ROOT/'examples/demo/author-pack.json'
PACK=load_pack(PACK_PATH)


class RealEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
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
        from her_agent.comparison import CorpusWeb, RunStopped
        from her_agent.web_lookup import PublicQuery
        corpus=deepcopy(self.corpus)
        for row in corpus['queries']:
            row['record_ids']=[];row['uncovered']=True
        web=CorpusWeb(corpus,'normal')
        with self.assertRaises(RunStopped): await web.search(PublicQuery('usc_exposition','architecture'))
        self.assertEqual(web.calls,[])

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
        with patch('her_agent.comparison_live.load_env',side_effect=AssertionError('no config')),patch('her_agent.comparison_live.configured_providers',side_effect=AssertionError('no provider')):
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
