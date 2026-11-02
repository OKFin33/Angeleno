"""Synthetic-only regression: no real trial data, account or external provider."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from her_agent.memory import MemoryStore
from her_agent.runtime import ConversationRuntime, open_store, non_action_opening

class Pack:
    id='fixture'
    def provenance(self): return {'fixture':'isolation'}
    def messages(self, history, variant='guided', memory_context=None, language=None):
        return [{'role':'system','content':'A fictional friend. '+json.dumps(memory_context,ensure_ascii=False)},*history]
class Provider:
    def __init__(self): self.calls=[]
    async def chat(self,messages,**kwargs):
        self.calls.append(messages)
        yield '在啊，怎么了？'

class IsolationTests(unittest.TestCase):
    def test_quarantine_preserves_raw_and_real_memory_but_excludes_synthetic_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'memory.sqlite3'
            s=MemoryStore(path,character='fixture',user='u',seed='s',purpose='real_user')
            a=s.append('D','user','河岸摄影展，周末再查');b=s.append('D','assistant','河岸摄影展待查')
            s.remember_lookup('D',{'user_turn':a,'request_id':'synthetic-r1','status':'insufficient','subject':'河岸摄影展','delivered_text':'河岸摄影展待查','conclusion':None,'applicable_date':None,'checked_at':'2026-01-01T00:00:00Z'})
            c=s.append('U','user','我喜欢安静');d=s.append('U','assistant','记得你喜欢安静')
            s.apply(s.batch(),{'retire':[],'updates':[
                {'id':'episode.development','kind':'episode','text':'河岸摄影展待查','keywords':['河岸摄影展'],'evidence':[a,b],'known':True},
                {'id':'card.user','kind':'user','text':'用户明确喜欢安静','keywords':['安静'],'evidence':[c],'known':True}]})
            result=s.quarantine_synthetic([a,b],reason='synthetic fixture D')
            self.assertEqual(result['record_ids'],['episode.development'])
            self.assertEqual(s.lookup_results(),[])
            self.assertEqual(s._meta('schema'),'3')
            s.append('U2','user','欸，你在吗？陪我说两句')
            context,history=s.context('U2','欸，你在吗？陪我说两句')
            self.assertNotIn('河岸摄影展',json.dumps([context,history],ensure_ascii=False))
            self.assertIn('喜欢安静',json.dumps(context,ensure_ascii=False))
            self.assertNotIn('河岸摄影展',json.dumps(s.batch().events,ensure_ascii=False))
            self.assertNotIn('河岸摄影展',json.dumps(s.search('河岸摄影展',budget=4000),ensure_ascii=False))
            self.assertEqual(s.db.execute("SELECT text FROM events WHERE seq=?",(a,)).fetchone()[0],'河岸摄影展，周末再查')
            s.close()
            s=MemoryStore(path,character='fixture',user='u',seed='s',purpose='real_user')
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM quarantined_events').fetchone()[0],2);s.close()
    def test_mixed_source_summary_is_not_automatically_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=MemoryStore(Path(tmp)/'memory.sqlite3',character='f',user='u',seed='s')
            a=s.append('D','user','合成输入');b=s.append('U','user','真实独立纠正')
            s.apply(s.batch(),{'retire':[],'updates':[{'id':'card.user','kind':'user','text':'混合摘要','keywords':[],'evidence':[a,b],'known':True}]})
            with self.assertRaisesRegex(ValueError,'Mixed-source'):s.quarantine_synthetic([a],reason='fixture')
            self.assertEqual(s.db.execute('SELECT delivery FROM events WHERE seq=?',(a,)).fetchone()[0],'complete');s.close()
    def test_development_or_unlabelled_open_cannot_write_real_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Pack();s=open_store(tmp,p,'same-id',create=True,purpose='real_user');s.append('U','user','合法资料');s.close()
            for purpose in ('development',None):
                with self.assertRaisesRegex(ValueError,'purpose differs'):open_store(tmp,p,'same-id',purpose=purpose)
            s=open_store(tmp,p,'dev-id',create=True,data_dir='private/development-state',purpose='development');s.append('D','user','独立合成输入');s.close()
            s=open_store(tmp,p,'same-id',purpose='real_user');self.assertEqual(s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1);s.close()
    def test_phatic_gate_is_narrow_not_a_global_search_ban(self):
        for text in ('Ya.','你好','嗯哼，刚忙完','欸，你在吗？陪我说两句'):
            self.assertTrue(non_action_opening(text),text)
        for text in ('行，查一下','接着看昨天那个河岸摄影展，找周末场次','我马上去露天电影','你好，查图书馆今晚时间'):
            self.assertFalse(non_action_opening(text),text)

class Lookup:
    def record_if_delivered(self,*args): pass
    def cancel_pending(self): pass

class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_call_ack_cannot_run_old_tools_but_explicit_and_same_call_confirmation_can(self):
        async def reply(*args,**kwargs):
            called.append(kwargs['utterance']);yield '我看一下。'
        with tempfile.TemporaryDirectory() as tmp:
            provider=Provider();s=open_store(tmp,Pack(),'u',create=True,purpose='real_user')
            s.append('old','user','有空再看河岸摄影展，先不查')
            rt=ConversationRuntime(Pack(),provider,store=s,writer=Provider(),lookup=Lookup())
            called=[]
            with patch('her_agent.lookup.reply',reply):
                t=rt.begin('嗯哼，刚忙完');text=''.join([x async for x in rt.stream(t)]);rt.delivered(t,text,complete=True)
                self.assertFalse(called);self.assertEqual(len(provider.calls),1)
                t=rt.begin('图书馆今晚时间，看看吧');text=''.join([x async for x in rt.stream(t)]);rt.delivered(t,text,complete=True)
                t=rt.begin('行，查一下');text=''.join([x async for x in rt.stream(t)])
                self.assertEqual(called,['图书馆今晚时间，看看吧','行，查一下'])
                rt.delivered(t,text,complete=True)
            await rt.close(flush=False)
            s=open_store(tmp,Pack(),'u',purpose='real_user');rt=ConversationRuntime(Pack(),provider,store=s,writer=Provider(),lookup=Lookup())
            with patch('her_agent.lookup.reply',reply):
                t=rt.begin('接着看昨天那个河岸摄影展，找周末场次');text=''.join([x async for x in rt.stream(t)]);rt.delivered(t,text,complete=True)
                self.assertEqual(called[-1],'接着看昨天那个河岸摄影展，找周末场次')
            await rt.close(flush=False)
