"""Exercise deadlines through actual SDK cancellation, TTS and audio outputs.

The clock limits are shortened in tests. Audio and HTTP are synthetic; none of
these tests claims human audibility or measures production latency.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from livekit import rtc
from livekit.agents import Agent, AgentSession
from livekit.agents.voice import io
from livekit.agents.voice.transcription import TranscriptSynchronizer

from angeleno.lookup import TextLookup
from angeleno.places import FixedClock, load_bundle
from angeleno.provider import TextDelta
from angeleno.runtime import ConversationRuntime, open_store
from angeleno.voice import VoiceBridge, HerVoiceLLM, UNCERTAIN_DELIVERY
from angeleno.voice_cards import VoiceCards, VoiceStatus, STATUS_TOPIC
from angeleno.voice_timing import AUDIO_TAG, ORDINARY_SECONDS, LOOKUP_SECONDS, PhaseAudioOutput, tagged_tts, install_phase_audio
from angeleno.worker.voxcpm_tts import VoxCPMHttpTTS
from test_lookup import Scripted, IdleWriter, call, check_args
from test_memory import pack_at
from test_voice_lifecycle import SyntheticAudio


def frame():
    return rtc.AudioFrame(data=bytes(960), sample_rate=24000, num_channels=1, samples_per_channel=480)


class AudibleSink(SyntheticAudio):
    finish_handle = None

    async def capture_frame(self, audio):
        first = not self.pending
        if first:
            self.duration = 0
        await super().capture_frame(audio)
        if first:
            self.on_playback_started(created_at=time.time())

    def flush(self):
        io.AudioOutput.flush(self)
        if not self.hold and self.pending and self.finish_handle is None:
            # The real room sink finishes asynchronously after its flush.
            def finish():
                self.finish_handle = None
                self.pending = False
                self.on_playback_finished(playback_position=self.duration, interrupted=False)
            self.finish_handle = asyncio.get_running_loop().call_soon(finish)

    def clear_buffer(self):
        if self.finish_handle is not None:
            self.finish_handle.cancel()
            self.finish_handle = None
        super().clear_buffer()


class Synthesizer:
    def __init__(self):
        self.block_text = None
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    def synthesize(self, text, *, conn_options):
        owner = self
        assert conn_options.max_retry == 0
        class Stream:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                owner.closed.set()
            def __aiter__(self):
                return self.audio()
            async def audio(self):
                owner.started.set()
                if owner.block_text and owner.block_text in text:
                    await asyncio.Event().wait()
                yield SimpleNamespace(frame=frame())
        return Stream()


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pack = pack_at(root)
        self.lookup = TextLookup(load_bundle(), FixedClock(datetime(2026, 10, 30, tzinfo=timezone.utc)))
        self.actor = Scripted(self.lookup)
        self.runtime = ConversationRuntime(self.pack, self.actor,
            store=open_store(root, self.pack, "deadlines", create=True), writer=IdleWriter(), lookup=self.lookup)
        self.bridge = VoiceBridge(self.runtime, ordinary_seconds=.15, lookup_seconds=.3)
        self.synth = Synthesizer()
        self.sink = AudibleSink()
        self.output = PhaseAudioOutput(self.sink, self.bridge)
        self.statuses = []
        self.bridge.status = lambda state, request: self.statuses.append((state, request))
        self.session = AgentSession(tts_text_transforms=None,
                                    turn_handling={"preemptive_generation": {"enabled": False}})
        self.bridge.bind(self.session)
        self.session.output.audio = self.output
        self.output.on("playback_finished", self.bridge.playback_finished)
        owner = self
        class TestAgent(Agent):
            def tts_node(self, text, model_settings):
                return tagged_tts(text, owner.bridge, owner.synth)
        await self.session.start(TestAgent(instructions="transport", llm=HerVoiceLLM(self.bridge)), record=False)

    async def asyncTearDown(self):
        await self.session.aclose()
        self.bridge.settle_unconfirmed()
        await self.bridge.deadlines.close()
        await self.runtime.close(flush=False)
        self.tmp.cleanup()

    async def reply(self, text="合成测试。"):
        speech = self.session.generate_reply(user_input=text)
        await asyncio.wait_for(speech.wait_for_playout(), 2)
        if self.bridge.deadlines.current and self.bridge.deadlines.current.expired:
            await asyncio.wait_for(asyncio.shield(self.bridge.deadlines.current.timeout_task), 1)
        return speech

    async def resumes(self):
        self.actor.rounds = [[TextDelta("下一轮能继续。")]]
        self.synth.block_text = None
        speech = await self.reply("继续吧。")
        self.assertFalse(speech.interrupted)
        self.assertTrue(self.bridge.last_complete)
        self.assertFalse(self.bridge.failed)

    async def test_normal_audio_retires_guard_and_defaults_match_spec(self):
        self.assertEqual((ORDINARY_SECONDS, LOOKUP_SECONDS), (15, 30))
        self.actor.rounds = [[TextDelta("合成普通回答。")]]
        await self.reply()
        timed = self.bridge.deadlines.evidence[-1]
        self.assertIsNotNone(timed.first_audio_at)
        self.assertFalse(timed.expired)
        self.assertTrue(timed.finished)
        self.assertNotIn("timeout", str(self.statuses))
        self.assertTrue(self.bridge.last_complete)

    async def test_model_stall_interrupts_only_that_turn_and_next_turn_continues(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        original = self.actor.chat_events
        async def blocked(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
                yield TextDelta("不得迟到")
            finally:
                stopped.set()
        self.actor.chat_events = blocked
        speech = await self.reply()
        self.assertTrue(started.is_set() and stopped.is_set())
        self.assertTrue(speech.interrupted)
        self.assertEqual(self.statuses[-1][0], "response_timeout")
        self.assertFalse(self.bridge.last_complete)
        self.assertEqual(self.runtime.store.pending()[-1]["text"], UNCERTAIN_DELIVERY)
        self.actor.chat_events = original
        await self.resumes()

    async def test_spoken_prompt_before_incomplete_tool_call_remains_bounded(self):
        stopped = asyncio.Event()
        original = self.actor.chat_events
        async def prompt_then_stall(*args, **kwargs):
            try:
                yield TextDelta("让我核对。")
                # No completed ToolCalls event: arguments or the upstream
                # stream can stall after audible text but before place_check.
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.actor.chat_events = prompt_then_stall
        speech = await self.reply()
        timed = self.bridge.deadlines.evidence[-1]
        self.assertIsNotNone(timed.first_audio_at)
        self.assertFalse(timed.lookup)
        self.assertTrue(timed.expired)
        self.assertTrue(stopped.is_set())
        expired_at = next(e["after_confirmed_ms"] for e in timed.events
                          if e["event"] == "response_timeout")
        self.assertGreaterEqual(expired_at, 290)
        self.assertLess(expired_at, 500)
        self.assertTrue(speech.interrupted)
        self.assertFalse(self.bridge.last_complete)
        self.assertEqual(self.lookup.checked_events, [])
        self.assertEqual(self.runtime.store.lookup_results(), [])
        self.actor.chat_events = original
        await self.resumes()

    async def test_actual_voxcpm_http_stall_closes_response_before_retry_and_resumes(self):
        closed = asyncio.Event()
        class Response:
            status = 200
            async def __aenter__(self):
                self.content = self
                return self
            async def __aexit__(self, *args):
                closed.set()
            async def iter_chunked(self, _):
                await asyncio.Event().wait()
                yield b"never"
        requests = []
        class Client:
            def post(self, *args, **kwargs):
                requests.append((args, kwargs))
                return Response()
        tts = VoxCPMHttpTTS(url="http://127.0.0.1:18080")
        tts._ensure_session = AsyncMock(return_value=Client())
        self.synth = tts
        self.actor.rounds = [[TextDelta("模型已经生成，声音仍未返回。")]]
        speech = await self.reply()
        self.assertTrue(speech.interrupted)
        self.assertTrue(closed.is_set())
        self.assertEqual(len(requests), 1)
        timed = self.bridge.deadlines.evidence[-1]
        self.assertTrue(timed.expired)
        self.assertIsNone(timed.first_audio_at, "generated text does not count as audio")
        self.assertNotIn("声音仍未返回", str(self.runtime.store.pending()))
        self.synth = Synthesizer()
        await self.resumes()

    async def test_spoken_prompt_does_not_satisfy_or_restart_lookup_deadline(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        class BlockedReader:
            async def check_async(inner, intent):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
        self.lookup.checker = BlockedReader()
        self.actor.rounds = [[TextDelta("让我核对。"), call("place_check", check_args("2026-10-31"))]]
        speech = await self.reply("核对一下 Fisher。")
        timed = self.bridge.deadlines.evidence[-1]
        self.assertTrue(started.is_set() and stopped.is_set())
        self.assertIsNotNone(timed.first_audio_at)
        self.assertIsNone(timed.answer_audio_at)
        self.assertTrue(speech.interrupted)
        self.assertEqual(self.statuses[-1][0], "lookup_timeout")
        deadline = next(e["after_confirmed_ms"] for e in timed.events if e["event"] == "lookup_timeout")
        self.assertGreaterEqual(deadline, 290)
        self.assertLess(deadline, 500)
        self.assertEqual(self.lookup.channel.card(self.runtime.conversation.session).status, "cancelled")
        self.assertEqual(self.runtime.store.lookup_results(), [])
        await self.resumes()

    async def test_output_stall_before_first_frame_does_not_hold_the_next_turn(self):
        capture = self.sink.capture_frame
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def waiting_for_subscriber(audio):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.sink.capture_frame = waiting_for_subscriber
        self.actor.rounds = [[TextDelta("还没送到输出设备。")]]
        speech = await self.reply()
        self.assertTrue(entered.is_set() and cancelled.is_set())
        self.assertTrue(speech.interrupted)
        self.assertIsNone(self.bridge.deadlines.evidence[-1].first_audio_at)
        self.assertEqual(self.output.captured_playout_segments, 0)
        self.assertFalse(self.bridge.last_complete)
        self.sink.capture_frame = capture
        await self.resumes()

    async def test_result_card_and_waiting_audio_do_not_mask_stalled_answer_tts(self):
        self.actor.rounds = [[TextDelta("让我核对。"), call("place_check", check_args("2026-10-31"))],
                             [TextDelta("结果回答。")]]
        self.synth.block_text = "结果"
        speech = await self.reply()
        timed = self.bridge.deadlines.evidence[-1]
        self.assertTrue(speech.interrupted)
        self.assertIsNotNone(timed.first_audio_at)
        self.assertIsNone(timed.answer_audio_at)
        self.assertTrue(timed.expired)
        self.assertEqual(self.lookup.channel.card(self.runtime.conversation.session).status, "supported")
        self.assertEqual(self.runtime.store.lookup_results(), [], "a card is not completed speech")
        await self.resumes()

    async def test_output_stall_after_prompt_drain_settles_and_resumes(self):
        capture = self.sink.capture_frame
        blocked = asyncio.Event()
        async def stall_answer(audio):
            if audio.userdata[AUDIO_TAG][1] == "answer":
                blocked.set()
                await asyncio.Event().wait()
            await capture(audio)
        self.sink.capture_frame = stall_answer
        self.actor.rounds = [[TextDelta("让我核对。"), call("place_check", check_args("2026-10-31"))],
                             [TextDelta("设备未接受的回答。")]]
        speech = await self.reply()
        self.assertTrue(blocked.is_set())
        self.assertTrue(speech.interrupted)
        self.assertFalse(self.bridge.last_complete)
        self.assertIsNone(self.bridge.deadlines.current.answer_audio_at)
        self.assertEqual(self.runtime.store.lookup_results(), [])
        self.sink.capture_frame = capture
        await self.resumes()

    async def test_answer_audio_starts_its_own_playback_segment_after_waiting_audio(self):
        self.actor.rounds = [[TextDelta("让我核对。"), call("place_check", check_args("2026-10-31"))],
                             [TextDelta("有依据的合成回答。")]]
        self.sink.hold = True
        speech = self.session.generate_reply(user_input="合成的查证问题。")
        await asyncio.wait_for(self.sink.frames.wait(), 1)
        await asyncio.sleep(.03)
        timed = self.bridge.deadlines.current
        self.assertIsNotNone(timed.first_audio_at)
        self.assertIsNone(timed.answer_audio_at, "buffered answer frames are not playback")
        self.sink.hold = False
        self.sink.flush()
        await asyncio.wait_for(speech.wait_for_playout(), 1)
        self.assertIsNotNone(timed.answer_audio_at)
        self.assertGreater(timed.answer_audio_at, timed.first_audio_at)
        self.assertFalse(timed.expired)
        self.assertTrue(self.bridge.last_complete)
        self.assertEqual(len(self.runtime.store.lookup_results()), 1)

    async def test_lookup_keeps_the_room_transcript_synchronizer_complete(self):
        class TextSink(io.TextOutput):
            def __init__(self):
                super().__init__(label="synthetic-transcript", next_in_chain=None)
                self.text = ""
            async def capture_text(self, text):
                self.text += text
            def flush(self):
                pass
        transcript = TextSink()
        self.sink = AudibleSink()
        sync = TranscriptSynchronizer(next_in_chain_audio=self.sink, next_in_chain_text=transcript)
        self.session.output.audio = sync.audio_output
        self.session.output.transcription = sync.text_output
        self.output = install_phase_audio(self.session, self.bridge)
        self.bridge.deadlines.ordinary, self.bridge.deadlines.lookup = 1, 2
        self.actor.rounds = [[TextDelta("让我核对。"), call("place_check", check_args("2026-10-31"))],
                             [TextDelta("有依据的合成回答。")]]
        try:
            await self.reply()
            await sync.barrier()
            self.assertTrue(self.bridge.last_complete)
            self.assertEqual(transcript.text, "让我核对。有依据的合成回答。")
            self.assertEqual(len(self.runtime.store.lookup_results()), 1)
            # With the production wrapper ordering, an answer-TTS timeout must
            # not publish the unspoken answer as subtitles or delivered memory.
            self.bridge.deadlines.ordinary, self.bridge.deadlines.lookup = .15, .3
            self.actor.rounds = [[TextDelta("再次核对。"), call("place_check", check_args("2026-10-31"))],
                                 [TextDelta("未播放的结果。")]]
            self.synth.block_text = "未播放"
            speech = await self.reply("再查一次。")
            await sync.barrier()
            self.assertTrue(speech.interrupted)
            self.assertFalse(self.bridge.last_complete)
            self.assertNotIn("未播放", transcript.text)
            self.assertEqual(len(self.runtime.store.lookup_results()), 1)
            await self.resumes()
            await sync.barrier()
            self.assertIn("下一轮能继续。", transcript.text)
        finally:
            await sync.aclose()

    async def test_late_audio_from_expired_turn_cannot_enter_new_turn(self):
        self.actor.rounds = [[TextDelta("卡住的旧回答。")]]
        self.synth.block_text = "卡住"
        await self.reply()
        old = self.bridge.deadlines.evidence[-1].request_id
        stale = frame()
        stale.userdata[AUDIO_TAG] = (old, "answer")
        before = self.sink.duration
        with self.assertRaises(asyncio.CancelledError):
            await self.output.capture_frame(stale)
        self.assertEqual(self.sink.duration, before)
        await self.resumes()
        with self.assertRaises(asyncio.CancelledError):
            await self.output.capture_frame(stale)
        self.assertFalse(self.bridge.deadlines.evidence[-1].expired)

    async def test_asr_confirmation_anchors_deadline_before_generation_starts(self):
        self.bridge.transcribed(SimpleNamespace(is_final=True, transcript="合成延迟输入。"))
        confirmed = self.bridge.confirmed[1]
        await asyncio.sleep(.06)
        self.actor.rounds = [[TextDelta("卡住的回答。")]]
        self.synth.block_text = "卡住"
        await self.reply("合成延迟输入。")
        timed = self.bridge.deadlines.evidence[-1]
        self.assertEqual(timed.confirmed_at, confirmed)
        self.assertEqual(timed.anchor, "asr_final_received")
        event = next(e for e in timed.events if e["event"] == "response_timeout")
        self.assertGreaterEqual(event["after_confirmed_ms"], 140)
        self.assertGreaterEqual(timed.events[0]["after_confirmed_ms"], 50)

    async def test_already_expired_confirmation_does_not_start_a_model_request(self):
        self.bridge.confirmed = ("已确认的输入。", time.monotonic() - 1, "asr_final_received")
        speech = await self.reply("已确认的输入。")
        self.assertTrue(speech.interrupted)
        self.assertEqual(self.actor.sent, [])
        await self.resumes()

    async def test_late_frame_is_rejected_even_before_the_watch_task_runs(self):
        turn = self.bridge.begin("expired-before-watch", "合成输入。")
        self.bridge.deadlines.current.confirmed_at -= 1
        stale = frame()
        stale.userdata[AUDIO_TAG] = ("expired-before-watch", "ordinary")
        before = self.sink.duration
        with self.assertRaises(asyncio.CancelledError):
            await self.output.capture_frame(stale)
        self.assertEqual(self.sink.duration, before)
        await self.bridge.deadlines.current.timeout_task
        self.assertTrue(turn.settled)

    async def test_expected_source_unavailable_is_spoken_and_call_continues(self):
        original = self.lookup.checker
        class FailedSource:
            async def check_async(inner, intent):
                return original._event(intent, status="unavailable", detail="timeout")
        self.lookup.checker = FailedSource()
        self.actor.rounds = [[call("place_check", check_args("2026-10-31"))],
                             [TextDelta("来源暂时打不开，开放情况还不能确定。")]]
        await self.reply()
        timed = self.bridge.deadlines.evidence[-1]
        self.assertFalse(timed.expired)
        self.assertIsNotNone(timed.answer_audio_at)
        self.assertTrue(self.bridge.last_complete)
        self.assertEqual(self.runtime.store.lookup_results()[0]["status"], "unavailable")
        await self.resumes()

    async def test_temporary_timeout_adds_no_persistent_events_or_results(self):
        before = self.runtime.store.db.total_changes
        self.actor.rounds = [[TextDelta("卡住的临时回答。")]]
        self.synth.block_text = "卡住"
        await self.reply("别保存这次。")
        self.assertTrue(self.runtime.active.temporary)
        self.assertEqual(self.runtime.store.db.total_changes, before)
        self.assertEqual(self.runtime.store.lookup_results(), [])


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_addressed_ordered_text_free_and_closed_on_hangup(self):
        import json
        participant = SimpleNamespace(publish_data=AsyncMock())
        sender = VoiceStatus(participant, "user-0123456789abcdef", "isolated-session")
        try:
            sender.offer("started", "r1")
            sender.offer("lookup_timeout", "r1")
            for _ in range(10):
                await asyncio.sleep(0)
            request = participant.publish_data.await_args
            self.assertEqual(request.kwargs, {"reliable": True,
                "destination_identities": ["user-0123456789abcdef"], "topic": STATUS_TOPIC})
            payload = json.loads(request.args[0])
            self.assertEqual(payload["sequence"], 2)
            self.assertEqual(payload["state"], "lookup_timeout")
            self.assertEqual(set(payload), {"type", "recipient", "session_id", "sequence", "request_id", "state"})
            await sender.close()
            sender.offer("started", "r2")
            await asyncio.sleep(0)
            self.assertEqual(participant.publish_data.await_count, 1)
        finally:
            await sender.close()

    async def test_status_transport_failure_is_not_retried(self):
        participant = SimpleNamespace(publish_data=AsyncMock(side_effect=RuntimeError("synthetic transport failure")))
        sender = VoiceStatus(participant, "user-0123456789abcdef", "s")
        sender.on_error = Mock()
        try:
            sender.offer("response_timeout", "r1")
            await asyncio.wait_for(sender.task, 1)
            sender.on_error.assert_called_once_with()
            self.assertTrue(sender.closed)
            self.assertEqual(participant.publish_data.await_count, 1)
        finally:
            await sender.close()


if __name__ == "__main__":
    unittest.main()
