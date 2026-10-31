"""Synthetic SDK and addressed card transport; no microphones or paid services."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from livekit.agents import AgentSession

from her_agent.live_sources import live_lookup
from her_agent.lookup import TextLookup
from her_agent.places import FixedClock, load_bundle
from her_agent.provider import TextDelta
from her_agent.runtime import ConversationRuntime, open_store
from her_agent.voice import VoiceBridge, HerVoiceLLM, create_voice_runtime, start_voice_session
from her_agent.voice_cards import VoiceCards, TOPIC
from worker.web_server import create_room_and_token
from test_lookup import Scripted, IdleWriter, call, check_args
from test_memory import pack_at
from test_voice_lifecycle import SyntheticAgent, SyntheticAudio


class VoiceCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = pack_at(self.root)
        self.lookup = TextLookup(load_bundle(), FixedClock(datetime(2026, 10, 30, tzinfo=timezone.utc)))
        self.actor = Scripted(self.lookup)
        self.runtime = ConversationRuntime(self.pack, self.actor,
            store=open_store(self.root, self.pack, "cards", create=True), writer=IdleWriter(), lookup=self.lookup)
        self.participant = Mock()
        self.participant.publish_data = AsyncMock()
        self.sender = VoiceCards(self.lookup, self.participant, "user-0123456789abcdef")
        self.session = None

    async def asyncTearDown(self):
        await self.sender.close()
        if self.session:
            await self.session.aclose()
        await self.runtime.close(flush=False)
        self.tmp.cleanup()

    async def drain(self):
        # Wait for the single retained card, not a network timing estimate.
        for _ in range(20):
            await asyncio.sleep(0)
            if self.participant.publish_data.await_count:
                return
        self.fail("No card delivered")

    def packets(self):
        return [json.loads(c.args[0]) for c in self.participant.publish_data.await_args_list]

    async def test_real_sdk_playout_uses_shared_lookup_and_separate_addressed_cards(self):
        self.actor.rounds = [[call("place_check", check_args("2026-10-31"))], [TextDelta("合成核对回复。")]]
        bridge = VoiceBridge(self.runtime)
        self.session = AgentSession(turn_handling={"preemptive_generation": {"enabled": False}})
        bridge.bind(self.session)
        output = SyntheticAudio()
        self.session.output.audio = output
        output.on("playback_finished", bridge.playback_finished)
        spoken = []
        class CapturingAgent(SyntheticAgent):
            async def tts_node(self, text, model_settings):
                async def capture():
                    async for piece in text:
                        spoken.append(piece)
                        yield piece
                async for frame in super().tts_node(capture(), model_settings):
                    yield frame
        await self.session.start(CapturingAgent(instructions="transport", llm=HerVoiceLLM(bridge)), record=False)
        await asyncio.wait_for(self.session.generate_reply(user_input="周六去 Fisher？").wait_for_playout(), 3)
        await self.drain()
        self.assertEqual("".join(spoken), "合成核对回复。")
        self.assertTrue(bridge.last_complete)
        self.assertEqual(self.packets()[-1]["card"]["status"], "supported")
        for request in self.participant.publish_data.await_args_list:
            self.assertEqual(request.kwargs, {"reliable": True,
                "destination_identities": ["user-0123456789abcdef"], "topic": TOPIC})
        self.assertEqual(len(self.runtime.store.lookup_results()), 1)

    async def test_cancelled_read_that_ignores_cancellation_cannot_publish_late_result(self):
        started = asyncio.Event()
        fixture = self.lookup.checker
        class LateChecker:
            async def check_async(inner, intent):
                event = fixture.check(intent)
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return event
        self.lookup.checker = LateChecker()
        self.actor.rounds = [[call("place_check", check_args("2026-10-31"))]]
        turn = self.runtime.begin("查 Fisher，随后挂断。")
        async def consume():
            return [piece async for piece in self.runtime.stream(turn)]
        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.drain()
        self.assertEqual(self.lookup.channel.card(self.runtime.conversation.session).status, "cancelled")
        self.assertNotIn("supported", [p["card"]["status"] for p in self.packets()])
        self.assertEqual(len(self.actor.sent), 1)
        self.assertEqual(self.runtime.store.lookup_results(), [])

    async def test_cancel_notifies_without_waiting_for_reader(self):
        self.lookup.begin_turn("synthetic-session", 1)
        intent, _ = self.lookup._prepare_check(check_args("2026-10-31"),
            session_id="synthetic-session", user_turn=1, utterance="合成查询")
        self.lookup.request_cancel()
        await self.drain()
        self.assertTrue(self.lookup.channel.is_cancelled(intent.request_id))
        self.assertEqual(self.packets()[-1]["card"]["status"], "cancelled")
        await self.sender.close()
        count = self.participant.publish_data.await_count
        self.lookup.channel.open(session_id="later", user_turn=1, utterance="后来的", question="后来", question_type="hours")
        await asyncio.sleep(0)
        self.assertEqual(self.participant.publish_data.await_count, count)

    async def test_transport_failure_is_bounded_without_retry(self):
        self.participant.publish_data.side_effect = RuntimeError("synthetic transport error")
        fail = Mock()
        self.sender.on_error = fail
        self.lookup.channel.open(session_id="s", user_turn=1, utterance="合成", question="合成", question_type="hours")
        await asyncio.wait_for(self.sender.task, 1)
        self.assertEqual(self.sender.error, "RuntimeError")
        self.assertEqual(self.participant.publish_data.await_count, 1)
        fail.assert_called_once_with()

    async def test_live_clock_refreshes_across_la_midnight(self):
        service = live_lookup()
        with patch("her_agent.live_sources.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 1, 6, 59, tzinfo=timezone.utc)
            service.begin_turn("s", 1)
            self.assertEqual(service.clock.now().date().isoformat(), "2026-10-31")
            clock.now.return_value = datetime(2026, 11, 1, 7, 1, tzinfo=timezone.utc)
            service.begin_turn("s", 2)
            self.assertEqual(service.clock.now().date().isoformat(), "2026-11-01")
        self.assertIs(service.clock, service.checker.clock)

    async def test_voice_runtime_opt_in_uses_live_checker_and_shared_memory(self):
        env = {"ANGELENO_CONVERSATION": "1", "LLM_PROVIDER": "deepseek",
            "ANGELENO_AUTHOR_PACK": str(self.root / "pack.json"), "ANGELENO_MEMORY_USER": "factory",
            "ANGELENO_MEMORY_CREATE": "1", "ANGELENO_MEMORY_TOOLS": "1", "ANGELENO_PLACE_TOOLS": "1"}
        with patch("socket.socket", side_effect=AssertionError("No network during construction")):
            runtime = create_voice_runtime(env, root=self.root, provider_factory=lambda _: (self.actor, IdleWriter()))
        self.assertTrue(runtime.memory_tools)
        self.assertTrue(hasattr(runtime.lookup.checker, "check_async"))
        await runtime.close(flush=False)

    async def test_missing_dispatch_recipient_refuses_card_broadcast_and_closes_store(self):
        ctx = Mock()
        ctx.job.metadata = '{}'
        with patch("her_agent.voice.create_voice_runtime", return_value=self.runtime):
            with self.assertRaises(KeyError):
                await start_voice_session(ctx, Mock(), {})
        self.assertTrue(self.runtime.closed)
        ctx.room.local_participant.publish_data.assert_not_called()

    async def test_dispatch_binds_same_generated_identity_as_token(self):
        client = Mock()
        client.room.create_room = AsyncMock()
        client.agent_dispatch.create_dispatch = AsyncMock()
        connection = AsyncMock()
        connection.__aenter__.return_value = client
        with patch("livekit.api.LiveKitAPI", return_value=connection):
            result = await create_room_and_token({"ANGELENO_PAID_ENABLED": "1",
                "LIVEKIT_URL": "ws://synthetic.invalid", "LIVEKIT_API_KEY": "synthetic",
                "LIVEKIT_API_SECRET": "synthetic-local-test-secret-0123456789"})
        dispatch = client.agent_dispatch.create_dispatch.await_args.args[0]
        self.assertEqual(json.loads(dispatch.metadata), {"user_identity": result["identity"]})
        self.assertEqual(dispatch.room, result["room"])


if __name__ == "__main__":
    unittest.main()
