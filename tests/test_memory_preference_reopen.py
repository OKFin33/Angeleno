"""Replay real synthetic model proposals through once/apply/close/reopen."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from angeleno.continuity import Maintainer
from angeleno.runtime import open_store

class Pack:
    id='synthetic-role'
    as_of='2026-11-01'
    def provenance(self):return {'fixture':'preference-reopen'}
    def maintenance_material(self):return 'A fictional role whose own preferences are not user preferences.'
class CapturedWriter:
    def __init__(self,proposals):self.proposals=iter(proposals);self.calls=0
    async def chat(self,messages,**kwargs):
        self.calls+=1;yield json.dumps(next(self.proposals),ensure_ascii=False)

class PreferenceReopenTests(unittest.IsolatedAsyncioTestCase):
    async def test_temporary_fact_role_preference_and_user_correction_persist_in_right_domains(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/transient-preference-maintenance.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pack=Pack();writer=CapturedWriter(fixture['proposals'])
            def reopen(create=False):return open_store(root,pack,'dev-preference',create=create,data_dir='private/development-state',purpose='development')
            store=reopen(create=True)
            for phase,(user,role) in enumerate(fixture['exchanges']):
                clock=SimpleNamespace(now=lambda _:datetime(2026,11,1,20,51,7+2*phase,tzinfo=timezone.utc))
                with patch('angeleno.memory.datetime',clock):
                    u=store.append('synthetic-phase'+str(phase),'user',user)
                    a=store.append('synthetic-phase'+str(phase),'assistant',role)
                self.assertTrue(await Maintainer(store,pack,writer).once())
                store.close();store=reopen();records={x['id']:x for x in store.records()}
                self.assertEqual(records['card.character']['evidence'],[2])
                self.assertEqual(records['card.character']['text'],'Jo说她自己比较喜欢安静，太吵的时候会想走。')
                if phase==0:
                    self.assertEqual(records['card.user']['evidence'],[1])
                    self.assertIn('临时凑合',records['card.user']['text'])
                    self.assertIn('2026-11-01T20:51:07Z',records['card.user']['text'])
                    self.assertIn('问Jo',records['card.user']['text'])
                    self.assertNotIn('用户喜欢安静',records['card.user']['text'])
                else:
                    self.assertEqual(records['card.user']['evidence'],[1,3])
                    self.assertIn('本人更喜欢热闹，不是安静',records['card.user']['text'])
                    self.assertIn('不能当作自己的偏好',records['card.user']['text'])
                    context,_=store.context('synthetic-next-call','你记得我的偏好吗？')
                    user_card=next(x for x in context['records'] if x['id']=='card.user')
                    self.assertEqual(user_card,records['card.user'])
                    self.assertEqual([row[0] for row in store.db.execute('SELECT text FROM events ORDER BY seq')],[text for pair in fixture['exchanges'] for text in pair])
            self.assertEqual(writer.calls,2);store.close()
