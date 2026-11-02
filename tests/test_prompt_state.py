"""Effective prompt/state matrix; synthetic stores and providers, no service calls."""
import itertools
from pathlib import Path
import tempfile
import unittest

from angeleno.author import ASSISTANT_SETTING, assistant_control_pack, load_pack
from angeleno.capabilities import capability_contract, set_capabilities
from angeleno.language import requested_language
from angeleno.lookup import prepare_messages as places_messages
from angeleno.memory_tools import prepare_messages as memory_messages
from angeleno.runtime import ConversationRuntime, open_store
from angeleno.web_lookup import WebLookup
from test_lookup import IdleWriter

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT/'characters/jo/author-pack.json')


class PromptStateTests(unittest.TestCase):
    def test_capability_matrix_has_one_consistent_block(self):
        for pack, memory, search, lookup, language in itertools.product(
                (PACK, assistant_control_pack(PACK)), (False, True), (False, True), ('none', 'places', 'web'), ('en', 'zh')):
            if search and not memory:
                continue
            with self.subTest(role=pack.role,memory=memory,search=search,lookup=lookup,language=language):
                messages=pack.messages([{'role':'user','content':'synthetic current topic'}],
                    memory_context={} if memory else None,language=language)
                if search:
                    messages=memory_messages(messages)
                render={'places':places_messages,'web':WebLookup.prepare_messages}.get(lookup)
                if render:
                    messages=render(messages)
                    self.assertEqual(render(messages),messages)
                system=messages[0]['content']
                self.assertEqual(system.count('[运行能力 memory='),1)
                self.assertIn(capability_contract(memory=memory,memory_search=search,lookup=lookup),system)
                self.assertEqual('本次不提供地方或网页查询工具' in system,lookup=='none')
                self.assertNotIn('不得声称系统已查',system)
                self.assertIn(f'[Interaction language: {language}]',system)
                self.assertIn('没有预订、联系或通话外自主服务',system)
                if pack.role=='assistant':
                    selected=ASSISTANT_SETTING.replace('你是用中文自然交谈','你是用英语自然交谈') if language=='en' else ASSISTANT_SETTING
                    self.assertEqual(system.count(selected),1)
                    if language=='en':self.assertNotIn('你是用中文自然交谈',system)

    def test_missing_duplicate_or_invalid_capability_blocks_fail_without_guessing(self):
        for system in ['no runtime block',capability_contract()+'\n'+capability_contract()]:
            with self.assertRaises(ValueError):set_capabilities([{'role':'system','content':system}],lookup='web')
        with self.assertRaises(ValueError):memory_messages([{'role':'system','content':capability_contract()}])

    def test_explicit_language_requests_not_mentions_quotes_or_tool_text(self):
        for text,target in [('我们换成中文聊吧','zh'),('请用英语回答','en'),("Let's switch to Chinese.",'zh'),
                            ('Can we speak English from now on?','en'),('Please respond in Mandarin.','zh'),
                            ('我们换回英文聊吧，说说 Los Angeles。','en')]:
            self.assertEqual(requested_language(text,'en' if target=='zh' else 'zh'),target,text)
        for text in ['Chinese food around USC?', '我在学中文', 'Is Chinese hard?',
                     'My friend said, speak Chinese.', 'If we speak Chinese, would that help?',
                     '不要换成中文', '为什么她说“我们换成中文聊吧”？', '`Please respond in Chinese.`',
                     'The place is called 中文书店.', 'I do not want to switch to Chinese.',
                     'Please translate "use Chinese" into French.']:
            self.assertEqual(requested_language(text,'en'),'en',text)


class PlainProvider:
    async def chat(self,messages,**kwargs):
        yield "synthetic ordinary reply"


class SessionLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def finish(self,runtime,text,complete=True):
        turn=runtime.begin(text)
        output=''.join([p async for p in runtime.stream(turn)])
        runtime.delivered(turn,output if complete else '',complete=complete)
        return turn

    async def test_switch_survives_history_crop_persistent_temporary_and_plain(self):
        for persistent,temporary,initial in itertools.product((False,True),(False,True),('en','zh')):
            with tempfile.TemporaryDirectory() as tmp:
                store=open_store(Path(tmp),PACK,'dev-lang',create=True,purpose='development') if persistent else None
                runtime=ConversationRuntime(PACK,PlainProvider(),store=store,writer=IdleWriter() if store else None,language=initial)
                target='zh' if initial=='en' else 'en'
                directive='我们换成中文聊吧' if target=='zh' else "Let's speak English."
                try:
                    if temporary:runtime.set_temporary(True)
                    await self.finish(runtime,directive,complete=False)
                    for n in range(12):
                        turn=await self.finish(runtime,f'Ordinary topic {n}: USC and 洛杉矶.')
                        self.assertEqual(turn.language,target)
                        self.assertIn(f'[Interaction language: {target}]',turn.messages[0]['content'])
                    self.assertFalse(any(m['content']==directive for m in turn.messages if m['role']=='user'))
                    if store and temporary:self.assertEqual(store.pending(),[])
                finally:await runtime.close(flush=False)

    async def test_reopening_call_uses_configured_language_without_mutating_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            store=open_store(root,PACK,'dev-lang',create=True,purpose='development')
            runtime=ConversationRuntime(PACK,PlainProvider(),store=store,writer=IdleWriter(),language='en')
            await self.finish(runtime,'我们换成中文聊吧')
            before=[dict(e) for e in store.pending()]
            await runtime.close(flush=False)
            store=open_store(root,PACK,'dev-lang',purpose='development')
            runtime=ConversationRuntime(PACK,PlainProvider(),store=store,writer=IdleWriter(),language='en')
            try:
                turn=await self.finish(runtime,'Hey, back again.')
                self.assertEqual(turn.language,'en')
                self.assertIn('[Interaction language: en]',turn.messages[0]['content'])
                self.assertEqual(store.pending()[:len(before)],before)
            finally:await runtime.close(flush=False)
