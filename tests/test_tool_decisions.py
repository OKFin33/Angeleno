"""Opt-in synthetic tool traces distinguish decisions without private contents."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from her_agent import diagnostics
from her_agent.runtime import ConversationRuntime
from her_agent.provider import TextDelta,ToolCalls
from her_agent.web_lookup import WebLookup
from test_lookup import PACK,CLOCK,Scripted,call
from test_web_lookup import FakeWeb

class ToolMetadataTests(unittest.TestCase):
    def test_only_explicit_development_and_no_parameter_or_id_text(self):
        tool={'id':'PRIVATE_ID','function':{'name':'web_search','arguments':'PRIVATE_ARGUMENT_TEXT'}}
        with tempfile.TemporaryDirectory() as tmp,patch.object(diagnostics,'ROOT',Path(tmp)),patch.object(diagnostics,'_tool_trace_rows',0):
            private=Path(tmp)/'private';private.mkdir();target=private/'tool.jsonl'
            env={'ANGELENO_DIAGNOSTICS_FILE':str(target),'ANGELENO_TOOL_TRACE':'0','ANGELENO_MEMORY_PURPOSE':'development'}
            with patch.dict(os.environ,env):self.assertFalse(diagnostics.tool_trace('tool_proposed',tool,user_turn=1))
            with patch.dict(os.environ,{**env,'ANGELENO_TOOL_TRACE':'1','ANGELENO_MEMORY_PURPOSE':'real_user'}):
                self.assertFalse(diagnostics.tool_trace('tool_proposed',tool,user_turn=1))
            self.assertFalse(target.exists())
            with patch.dict(os.environ,{**env,'ANGELENO_TOOL_TRACE':'1'}):
                self.assertTrue(diagnostics.tool_trace('tool_proposed',tool,user_turn=1,reason='PRIVATE_REASON',status='PRIVATE_STATUS'))
            raw=target.read_text();row=json.loads(raw)
            self.assertNotIn('PRIVATE_',raw)
            self.assertEqual((row['operation'],row['reason'],row['status']),('web_search','tool_not_accepted','other'))
            self.assertEqual(len(row['argument_hash']),64);self.assertEqual(target.stat().st_mode&0o777,0o600)

    def test_trace_is_capped_without_affecting_service(self):
        with patch.dict(os.environ,{'ANGELENO_TOOL_TRACE':'1','ANGELENO_MEMORY_PURPOSE':'development','ANGELENO_DIAGNOSTICS_FILE':'private/trace.jsonl'}),patch.object(diagnostics,'_tool_trace_rows',0),patch.object(diagnostics,'emit',return_value=True) as emit:
            c={'id':'x','function':{'name':'web_search','arguments':'{}'}}
            for _ in range(256):self.assertTrue(diagnostics.tool_trace('tool_proposed',c,user_turn=1))
            self.assertFalse(diagnostics.tool_trace('tool_proposed',c,user_turn=1));self.assertEqual(emit.call_count,256)

class ToolTraceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self,rounds,*,enabled=True,api=None):
        actor=Scripted();actor.rounds=deepcopy(rounds);api=api or FakeWeb();service=WebLookup(api,CLOCK);service.local_session='synthetic-observer'
        runtime=ConversationRuntime(PACK,actor,lookup=service)
        with tempfile.TemporaryDirectory() as tmp,patch.object(diagnostics,'ROOT',Path(tmp)),patch.object(diagnostics,'_tool_trace_rows',0):
            directory=Path(tmp)/'private';directory.mkdir();target=directory/'tool.jsonl'
            with patch.dict(os.environ,{'ANGELENO_TOOL_TRACE':'1' if enabled else '0','ANGELENO_MEMORY_PURPOSE':'development','ANGELENO_DIAGNOSTICS_FILE':str(target)}):
                try:
                    turn=runtime.begin('Find public architecture exhibitions.');text=''.join([p async for p in runtime.stream(turn)])
                finally:await runtime.close(flush=False)
            rows=[json.loads(x) for x in target.read_text().splitlines()] if target.exists() else []
        return text,actor.sent,rows,api

    async def test_trace_does_not_change_inputs_outputs_or_adapter_calls(self):
        rounds=[[call('web_search',{'area':'usc_exposition','topic':'architecture'})],[TextDelta('Public fixture, not verified.')]]
        off=await self.run_case(rounds,enabled=False);on=await self.run_case(rounds)
        self.assertEqual(off[:2],on[:2]);self.assertEqual(off[3].requests,on[3].requests)
        self.assertEqual(off[2],[])
        self.assertEqual([r['event'] for r in on[2]],['tool_proposed','tool_validated','tool_adapter_start','tool_adapter_end','tool_result'])
        self.assertEqual(on[2][-1]['status'],'evidence')

    async def test_bad_batch_has_rejection_but_no_adapter_start(self):
        calls=call('web_search',{'area':'usc_exposition','topic':'architecture'}).calls
        rounds=[[ToolCalls((calls[0],calls[0]))]]
        with patch('her_agent.lookup.tool_trace') as trace:
            with self.assertRaisesRegex(ValueError,'Duplicate tool call ID'):await self.run_case(rounds)
        events=[c.args[0] for c in trace.call_args_list]
        self.assertIn('tool_rejected',events);self.assertNotIn('tool_adapter_start',events)

    async def test_over_limit_proposal_is_skipped_without_extra_execution(self):
        calls=tuple(call('web_search',{'area':'usc_exposition','topic':'architecture'},call_id='c'+str(i)).calls[0] for i in range(4))
        _,_,rows,api=await self.run_case([[ToolCalls(calls)],[TextDelta('Only executed evidence counts.')]])
        self.assertEqual(len(api.requests),3)
        skipped=[r for r in rows if r['event']=='tool_not_executed']
        self.assertEqual(len(skipped),1);self.assertEqual(skipped[0]['reason'],'tool_limit_reached')

    async def test_budget_rejection_is_distinct_from_cancel_or_parameter_rejection(self):
        from her_agent.budget import BudgetExceeded
        class BudgetRefused(FakeWeb):
            async def search(self,q):raise BudgetExceeded('Synthetic budget stop')
        rounds=[[call('web_search',{'area':'usc_exposition','topic':'architecture'})]]
        with patch('her_agent.lookup.tool_trace') as trace:
            with self.assertRaises(BudgetExceeded):await self.run_case(rounds,api=BudgetRefused())
        self.assertEqual(trace.call_args_list[-1].kwargs['reason'],'budget_refused')
        self.assertNotIn('tool_result',[c.args[0] for c in trace.call_args_list])

    async def test_cancelled_adapter_is_not_marked_as_a_result(self):
        class Cancelled(FakeWeb):
            async def search(self,q):raise asyncio.CancelledError
        rounds=[[call('web_search',{'area':'usc_exposition','topic':'architecture'})]]
        with patch('her_agent.lookup.tool_trace') as trace:
            with self.assertRaises(asyncio.CancelledError):await self.run_case(rounds,api=Cancelled())
        self.assertIn('tool_adapter_end',[c.args[0] for c in trace.call_args_list])
        self.assertNotIn('tool_result',[c.args[0] for c in trace.call_args_list])
        self.assertEqual(trace.call_args_list[-1].kwargs['reason'],'cancelled')
