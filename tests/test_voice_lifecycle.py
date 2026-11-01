"""Synthetic providers and actual LiveKit AgentSession playout, without services."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from livekit import rtc
from livekit.agents import Agent, AgentSession, llm
from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice import io
from livekit.agents.voice.events import ConversationItemAddedEvent
from her_agent.provider import TextDelta, ToolCalls

from her_agent.runtime import ConversationRuntime, open_store
from her_agent.voice import VoiceBridge, HerVoiceLLM, UNCERTAIN_DELIVERY, create_voice_runtime, start_voice_session
from test_memory import pack_at, record


class Provider:
    model = "synthetic"
    max_tokens = 512

    def __init__(self, text="明确的合成回复。", wait=None):
        self.text, self.wait = text, wait
        self.requests = []
        self.closed = False

    async def chat(self, messages, **kwargs):
        self.requests.append(messages)
        yield self.text
        if self.wait:
            await self.wait.wait()

    async def aclose(self):
        self.closed = True


class SyntheticAudio(io.AudioOutput):
    def __init__(self, *, hold=False, synchronized=None):
        super().__init__(label="synthetic", capabilities=io.AudioOutputCapabilities(pause=False))
        self.hold = hold
        self.synchronized = synchronized
        self.frames = asyncio.Event()
        self.duration = 0
        self.pending = False

    async def capture_frame(self, frame):
        await super().capture_frame(frame)
        self.duration += frame.duration
        self.pending = True
        self.frames.set()

    def flush(self):
        super().flush()
        if not self.hold and self.pending:
            self.pending = False
            self.on_playback_finished(playback_position=self.duration, interrupted=False)

    def clear_buffer(self):
        super().flush()
        if self.pending:
            self.pending = False
            self.on_playback_finished(playback_position=self.duration, interrupted=True,
                                      synchronized_transcript=self.synchronized)


class SyntheticAgent(Agent):
    async def tts_node(self, text, model_settings):
        async for piece in text:
            # 20 ms of synthetic silence represents each streamed text chunk.
            yield rtc.AudioFrame(data=bytes(960), sample_rate=24000, num_channels=1, samples_per_channel=480)


class VoiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = pack_at(self.root)
        self.actor = Provider()
        self.writer = Provider('{"retire": [], "updates": []}')
        self.runtime = None
        self.session = None

    async def asyncTearDown(self):
        if self.session:
            await self.session.aclose()
        if self.runtime and not self.runtime.closed:
            await self.runtime.close(flush=False)
        self.tmp.cleanup()

    def core(self, **kwargs):
        self.runtime = ConversationRuntime(self.pack, self.actor,
            store=open_store(self.root, self.pack, "voice-test", create=True), writer=self.writer, **kwargs)
        return self.runtime

    async def live(self, *, hold=False, synchronized=None):
        bridge = VoiceBridge(self.core())
        self.session = AgentSession(turn_handling={"preemptive_generation": {"enabled": False}})
        bridge.bind(self.session)
        output = SyntheticAudio(hold=hold, synchronized=synchronized)
        self.session.output.audio = output
        output.on("playback_finished", bridge.playback_finished)
        await self.session.start(SyntheticAgent(instructions="transport only", llm=HerVoiceLLM(bridge)), record=False)
        return bridge, output

    async def test_real_session_full_playout_and_redial_pending_context(self):
        bridge, output = await self.live()
        speech = self.session.generate_reply(user_input="这周准备合成考试。")
        await asyncio.wait_for(speech.wait_for_playout(), 3)
        self.assertTrue(bridge.last_complete)
        self.assertEqual([e["delivery"] for e in self.runtime.store.pending()], ["complete", "complete"])
        self.assertEqual(len(self.actor.requests), 1)
        self.assertNotIn("transport only", str(self.actor.requests))
        await self.session.aclose()
        await self.runtime.close(flush=False)
        self.session = None
        self.core()
        turn = self.runtime.begin("刚才聊到什么？")
        self.assertIn("这周准备合成考试", str(turn.messages))
        self.assertIn(self.actor.text, str(turn.messages))
        self.assertNotIn("transport only", str(turn.messages))

    async def test_real_session_hangup_without_alignment_never_saves_generated_full_text(self):
        bridge, output = await self.live(hold=True)
        self.session.generate_reply(user_input="合成挂断测试。")
        await asyncio.wait_for(output.frames.wait(), 3)
        await asyncio.wait_for(self.session.aclose(), 3)
        bridge.settle_unconfirmed()
        events = self.runtime.store.pending()
        self.assertFalse(bridge.last_complete)
        self.assertEqual(events[-1]["delivery"], "interrupted")
        self.assertEqual(events[-1]["text"], UNCERTAIN_DELIVERY)
        self.assertNotIn(self.actor.text, str(events))
        await self.runtime.close(flush=False)
        self.assertEqual(self.writer.requests, [])

    async def test_real_session_interrupt_uses_only_synchronized_prefix(self):
        bridge, output = await self.live(hold=True, synchronized="明确的")
        speech = self.session.generate_reply(user_input="合成打断测试。")
        await asyncio.wait_for(output.frames.wait(), 3)
        await asyncio.wait_for(self.session.interrupt(), 3)
        self.assertTrue(speech.interrupted)
        events = self.runtime.store.pending()
        self.assertEqual(events[-1]["text"], "明确的")
        self.assertEqual(events[-1]["delivery"], "interrupted")
        output.hold = False
        await asyncio.wait_for(self.session.generate_reply(user_input="继续合成测试。").wait_for_playout(), 3)
        self.assertTrue(bridge.last_complete)
        self.assertIn("明确的", str(self.actor.requests[-1]))
        self.assertNotIn("明确的合成回复", str(self.actor.requests[-1]))

    async def test_maintenance_timeout_and_failure_keep_evidence_and_release_identity(self):
        for text, blocked in (("bad json", False), ('{"retire": [], "updates": []}', True)):
            with self.subTest(blocked=blocked):
                self.writer = Provider(text, asyncio.Event() if blocked else None)
                runtime = self.core()
                turn = runtime.begin("明确合成输入")
                displayed = "".join([piece async for piece in runtime.stream(turn)])
                runtime.delivered(turn, displayed, complete=True)
                await runtime.close(timeout=.03)
                self.assertIsNotNone(runtime.maintainer.error)
                self.assertEqual(len(self.writer.requests), 1)
                self.assertTrue(self.writer.closed)
                store = open_store(self.root, self.pack, "voice-test")
                self.assertTrue(store.pending())
                self.assertEqual(store.watermark, 0)
                store.close()

    async def test_missing_or_locked_identity_refuses_before_provider_factory(self):
        env = {"ANGELENO_CONVERSATION": "1", "ANGELENO_AUTHOR_PACK": str(self.root / "pack.json"),
               "LLM_PROVIDER": "deepseek", "ANGELENO_MEMORY_PURPOSE": "real_user", "ANGELENO_MEMORY_CREATE": "1"}
        called = []
        factory = lambda env: called.append(True)
        with self.assertRaisesRegex(ValueError, "ANGELENO_MEMORY_USER"):
            create_voice_runtime(env, root=self.root, provider_factory=factory)
        self.core()
        env["ANGELENO_MEMORY_USER"] = "voice-test"
        with self.assertRaisesRegex(ValueError, "already open"):
            create_voice_runtime(env, root=self.root, provider_factory=factory)
        self.assertEqual(called, [])

    async def test_memory_tool_protocol_stays_inside_core(self):
        class SearchProvider(Provider):
            async def chat_events(self, messages, **kwargs):
                self.requests.append(messages)
                if kwargs.get("tools"):
                    yield ToolCalls(({"id": "search-1", "type": "function", "function": {
                        "name": "memory_search", "arguments": '{"query":"合成"}'}},))
                else:
                    yield TextDelta("这里没有相关的合成记忆。")
        self.actor = SearchProvider()
        runtime = self.core(memory_tools=True)
        turn = runtime.begin("还记得合成内容吗？")
        text = "".join([piece async for piece in runtime.stream(turn)])
        self.assertEqual(text, "这里没有相关的合成记忆。")
        self.assertEqual(len(self.actor.requests), 2)
        self.assertNotIn("memory_search", text)
        runtime.delivered(turn, text, complete=True)
        self.assertNotIn("search-1", str(runtime.store.pending()))

    async def test_real_session_actor_failure_closes_without_retry_or_maintenance(self):
        class BrokenProvider(Provider):
            async def chat(self, messages, **kwargs):
                self.requests.append(messages)
                raise RuntimeError("synthetic failed foreground")
                yield "never"
        self.actor = BrokenProvider()
        bridge, output = await self.live()
        closed = asyncio.Event()
        self.session.on("close", lambda event: closed.set())
        self.session.generate_reply(user_input="合成失败测试。")
        await asyncio.wait_for(closed.wait(), 3)
        bridge.settle_unconfirmed()
        await self.runtime.close(flush=False)
        self.assertTrue(bridge.failed)
        self.assertEqual(len(self.actor.requests), 1)
        self.assertEqual(self.writer.requests, [])
        self.assertTrue(self.actor.closed)

    async def test_job_cleanup_waits_for_session_close_and_releases_identity(self):
        runtime = self.core()
        class Context:
            room = SimpleNamespace(local_participant=SimpleNamespace(publish_data=AsyncMock()))
            job = SimpleNamespace(metadata='{"user_identity":"user-0123456789abcdef"}')
            callbacks = []
            stopped = asyncio.Event()
            def add_shutdown_callback(self, fn):
                self.callbacks.append(fn)
            def shutdown(self, reason):
                self.stopped.set()
        ctx = Context()
        # Use the actual entrypoint lifecycle, substituting only room-independent
        # model and output setup. No server, credentials or microphones are used.
        class Session(AgentSession):
            async def start(self, agent, **kwargs):
                self.output.audio = SyntheticAudio()
                return await super().start(agent, record=False)
        from worker.voxcpm_tts import VoxCPMHttpTTS
        owned_tts = VoxCPMHttpTTS(url="http://127.0.0.1:18080")
        # Allocate the real production owner's session without making HTTP calls.
        http_session = await owned_tts._ensure_session()
        self.addAsyncCleanup(owned_tts.aclose)
        def factory(instructions, llm_instance):
            class TaggedAgent(SyntheticAgent):
                async def tts_node(self, text, model_settings):
                    from her_agent.voice_timing import AUDIO_TAG
                    async for frame in super().tts_node(text, model_settings):
                        frame.userdata[AUDIO_TAG] = (llm_instance.bridge.active_id, "ordinary")
                        yield frame
            agent = TaggedAgent(instructions=instructions, llm=llm_instance)
            agent.voice_tts = owned_tts
            return agent
        with patch("her_agent.voice.create_voice_runtime", return_value=runtime), \
             patch("her_agent.voice.AgentSession", Session):
            self.session = await start_voice_session(ctx, factory, {})
        await asyncio.wait_for(self.session.generate_reply(user_input="正常结束的合成输入。").wait_for_playout(), 3)
        await self.session.aclose()
        await asyncio.wait_for(ctx.stopped.wait(), 3)
        await ctx.callbacks[0]("job ended")
        await ctx.callbacks[0]("repeated job shutdown")
        self.assertTrue(http_session.closed)
        self.assertIsNone(owned_tts._http_session)
        self.assertTrue(runtime.closed)
        self.assertEqual(len(self.writer.requests), 1)
        self.assertTrue(self.writer.closed)
        store = open_store(self.root, self.pack, "voice-test")
        self.assertEqual(store.pending(), [])
        store.close()

    async def test_failed_session_start_closes_owned_tts_and_runtime(self):
        runtime = self.core()
        class Context:
            room = SimpleNamespace(local_participant=SimpleNamespace(publish_data=AsyncMock()))
            job = SimpleNamespace(metadata='{"user_identity":"user-0123456789abcdef"}')
            def add_shutdown_callback(self, fn):
                self.finish = fn
        class Session(AgentSession):
            async def start(self, agent, **kwargs):
                raise RuntimeError("synthetic startup failure")
        from worker.voxcpm_tts import VoxCPMHttpTTS
        owned_tts = VoxCPMHttpTTS(url="http://127.0.0.1:18080")
        http_session = await owned_tts._ensure_session()
        self.addAsyncCleanup(owned_tts.aclose)
        def factory(instructions, llm_instance):
            agent = SyntheticAgent(instructions=instructions, llm=llm_instance)
            agent.voice_tts = owned_tts
            return agent
        ctx = Context()
        with patch("her_agent.voice.create_voice_runtime", return_value=runtime), \
             patch("her_agent.voice.AgentSession", Session):
            with self.assertRaisesRegex(RuntimeError, "synthetic startup failure"):
                await start_voice_session(ctx, factory, {})
        await ctx.finish("job ended")
        self.assertTrue(http_session.closed)
        self.assertTrue(runtime.closed)
        self.assertEqual(self.writer.requests, [])

    async def test_identity_seed_change_rejected_without_factory_or_reset(self):
        runtime = self.core()
        runtime.begin("保留的合成原文。")
        await runtime.close(flush=False)
        original = (self.root / "private/state/synthetic/voice-test/memory.sqlite3").read_bytes()
        (self.root / "character.md").write_text("改变过的合成人物原稿。")
        env = {"ANGELENO_CONVERSATION": "1", "ANGELENO_AUTHOR_PACK": str(self.root / "pack.json"),
               "LLM_PROVIDER": "deepseek", "ANGELENO_MEMORY_PURPOSE": "real_user", "ANGELENO_MEMORY_CREATE": "1", "ANGELENO_MEMORY_USER": "voice-test"}
        with self.assertRaises(ValueError):
            create_voice_runtime(env, root=self.root,
                                 provider_factory=lambda env: self.fail("No provider before compatibility"))
        self.assertEqual((self.root / "private/state/synthetic/voice-test/memory.sqlite3").read_bytes(), original)

    async def test_cancellation_before_tool_followup_starts_no_second_request(self):
        reached_tool = asyncio.Event()
        released = asyncio.Event()
        class SearchProvider(Provider):
            async def chat_events(self, messages, **kwargs):
                self.requests.append(messages)
                try:
                    yield ToolCalls(({"id": "search-cancel", "type": "function", "function": {
                        "name": "memory_search", "arguments": '{"query":"合成"}'}},))
                    reached_tool.set()
                    await asyncio.Event().wait()
                finally:
                    released.set()
        self.actor = SearchProvider()
        runtime = self.core(memory_tools=True)
        turn = runtime.begin("取消合成检索。")
        async def consume():
            return [piece async for piece in runtime.stream(turn)]
        task = asyncio.create_task(consume())
        await reached_tool.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(released.is_set())
        self.assertEqual(len(self.actor.requests), 1)
        await runtime.close(flush=False)
        self.assertEqual(self.writer.requests, [])

    async def test_maintained_d02_redial_keeps_raw_referent_without_search_keywords(self):
        # The public synthetic D02 prompts reproduce the observed real probe's
        # missing-input failure. Replies/proposal below are handwritten fixtures;
        # no private model output or quality judgment is asserted by this test.
        first = "今天路过一家修鞋铺，门口放着一双特别新的皮鞋。我愣了一下，新鞋也会拿去修啊。"
        corrected = "后来才看见，师傅是在给鞋底加一层防滑垫，根本不是坏了。"
        question = "刚才说的那家店，你还记得我一开始看错了什么吗？"
        self.writer = Provider(json.dumps({"retire": [], "updates": [record(
            "episode.shoes", "用户起初以为新皮鞋坏了，后来知道是在加防滑垫。",
            [1, 3], keywords=["修鞋", "皮鞋", "防滑垫"])]}, ensure_ascii=False))
        runtime = self.core()
        for user in (first, corrected):
            turn = runtime.begin(user)
            text = "".join([piece async for piece in runtime.stream(turn)])
            runtime.delivered(turn, text, complete=True)
        original_session = runtime.conversation.session
        original_created = "2026-10-28T10:00:00+00:00"
        with runtime.store.db:
            runtime.store.db.execute("UPDATE events SET created=?", (original_created,))
        await runtime.close(flush=True)
        self.assertEqual(len(self.writer.requests), 1)
        runtime = self.core()
        self.assertEqual(runtime.store.pending(), [])
        self.assertEqual(runtime.store.select(question), [])  # no lexical/fallback hit
        turn = runtime.begin(question)
        _ = [piece async for piece in runtime.stream(turn)]
        sent = self.actor.requests[-1]
        self.assertEqual([m["content"] for m in sent[1::2]], [first, corrected, question])
        context = json.loads(sent[0]["content"].rsplit("\n", 1)[1])
        self.assertEqual(context["records"], [])
        self.assertEqual(context["history_evidence"], [1, 2, 3, 4, 5])
        self.assertTrue(all(e["created"] == original_created and e["session"] == original_session
                            for e in context["history_sources"][:4]))
        self.assertNotEqual(context["history_sources"][-1]["session"], original_session)
        exposed = json.loads(runtime.store.db.execute("SELECT payload FROM turn_inputs WHERE turn=5").fetchone()[0])
        self.assertEqual(exposed["events"], [1, 2, 3, 4, 5])
        runtime.delivered(turn, self.actor.text, complete=True)
        runtime.store.forget("episode.shoes")
        self.assertEqual(runtime.store.pending(), [])  # includes the dependent redial
        self.assertNotIn("修鞋", str(runtime.store.context("later", question)))

    async def test_recent_redial_hides_entire_hidden_or_forgotten_turn(self):
        runtime = self.core()
        for user in ("HIDDEN_USER_TEST", "可见的合成输入"):
            turn = runtime.begin(user)
            text = "".join([piece async for piece in runtime.stream(turn)])
            runtime.delivered(turn, text, complete=True)
        runtime.store.apply(runtime.store.batch(), {"retire": [], "updates": [record(
            "episode.hidden", "不可知资料", [2], known=False)]})
        # Even retiring the hidden record cannot make its cited turn visible.
        with runtime.store.db:
            runtime.store.db.execute("UPDATE records SET active=0 WHERE id='episode.hidden'")
        await runtime.close(flush=False)
        runtime = self.core()
        turn = runtime.begin("你还记得刚才吗？")
        self.assertNotIn("HIDDEN_USER_TEST", str(turn.messages))
        context = json.loads(turn.messages[0]["content"].rsplit("\n", 1)[1])
        self.assertEqual(context["history_evidence"], [3, 4, 5])
        # Defensive partial-forgetting state must also hide a surviving partner.
        runtime.delivered(turn, "", complete=False)
        with runtime.store.db:
            runtime.store.db.execute("UPDATE events SET text='',delivery='forgotten' WHERE seq=4")
        after = runtime.begin("再试一次。")
        self.assertNotIn("可见的合成输入", str(after.messages))


if __name__ == "__main__":
    unittest.main()
