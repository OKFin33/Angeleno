"""LiveKit 1.8.4 adapter: SDK playout evidence feeds the shared Her core.

The room transcript is transport state only. It never compiles an actor prompt.
Playback events describe server-side playout, not proof of human attention.
"""

import asyncio
import json
import re
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

from livekit.agents import AgentSession, llm, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

from .baseline import configured_pack
from .runtime import ConversationRuntime, open_store
from .provider import configured_providers
from .voice_timing import TurnDeadlines, install_phase_audio


ROOT = Path(__file__).resolve().parents[1]
UNCERTAIN_DELIVERY = "[语音交付中断或未确认，无法确认对方已听到的文字；不据此建立人物事实]"


def voice_configuration(env, *, root=ROOT):
    pack = configured_pack(env, root=root)
    if pack is None:
        raise ValueError("ANGELENO_AUTHOR_PACK is required for the shared voice core")
    user = env.get("ANGELENO_MEMORY_USER", "").strip()
    if not user:
        raise ValueError("ANGELENO_MEMORY_USER must explicitly select an independent trial identity")
    variant = env.get("ANGELENO_AUTHOR_VARIANT", "guided")
    if variant not in {"raw", "guided"}:
        raise ValueError("Unknown ANGELENO_AUTHOR_VARIANT")
    for key in ("ANGELENO_MEMORY_CREATE", "ANGELENO_MEMORY_TOOLS", "ANGELENO_PLACE_TOOLS", "ANGELENO_WEB_ENABLED"):
        if env.get(key, "0") not in {"0", "1"}:
            raise ValueError(f"{key} must be 0 or 1")
    return pack, user, variant


def validate_voice_setup(env, *, root=ROOT):
    pack, user, _ = voice_configuration(env, root=root)
    store = open_store(root, pack, user, create=env.get("ANGELENO_MEMORY_CREATE") == "1",
                       data_dir=env.get("ANGELENO_DATA_DIR", "private/state"))
    store.close()


def create_voice_runtime(env, *, root=ROOT, provider_factory=None):
    pack, user, variant = voice_configuration(env, root=root)
    store = open_store(root, pack, user, create=env.get("ANGELENO_MEMORY_CREATE") == "1",
                       data_dir=env.get("ANGELENO_DATA_DIR", "private/state"))
    try:
        actor, writer = (provider_factory(env) if provider_factory else configured_providers(env, root=root))
        from .live_sources import live_lookup
        from .web_lookup import configured_web_lookup
        lookup = (configured_web_lookup(env, actor, pack=pack) if env.get("ANGELENO_WEB_ENABLED") == "1"
                  else live_lookup() if env.get("ANGELENO_PLACE_TOOLS") == "1" else None)
        return ConversationRuntime(pack, actor, store=store, writer=writer, variant=variant,
                                   memory_tools=env.get("ANGELENO_MEMORY_TOOLS") == "1",
                                   lookup=lookup)
    except BaseException:
        store.close()
        raise


class VoiceBridge:
    """Correlate one serialized SDK generation with its actual playout events."""

    def __init__(self, runtime, *, ordinary_seconds=15.0, lookup_seconds=30.0):
        self.runtime = runtime
        self.pending = {}
        self.playback = {}
        self.active_id = None
        self.session = None
        self.failed = False
        self.last_complete = True
        self.seen_items = set()
        self.deadlines = TurnDeadlines(self.expire, ordinary=ordinary_seconds, lookup=lookup_seconds)
        self.confirmed = None
        self.speech_parts = deque()
        self.status = None
        if runtime.lookup is not None:
            runtime.lookup.on_check_started = self.check_started
            runtime.lookup.on_check_finished = self.check_finished

    def begin(self, request_id, user):
        # SDK can suppress an assistant item if interrupted before audio starts.
        # It finishes cancellation before a subsequent user turn. Settle that
        # turn without pretending the generated buffer was played.
        self.settle_unconfirmed()
        turn = self.runtime.begin(user)
        self.pending[request_id] = turn
        self.playback[request_id] = []
        self.active_id = request_id
        self.last_complete = False
        confirmation = self.confirmed if self.confirmed and self.confirmed[0] == user else None
        self.confirmed = None
        self.speech_parts.clear()
        self.deadlines.begin(request_id, confirmed_at=confirmation[1] if confirmation else None,
                             anchor=confirmation[2] if confirmation else None)
        if self.status:
            self.status("started", request_id)
        return turn

    def transcribed(self, event):
        if event.is_final and event.transcript.strip():
            self.confirmed = (event.transcript, time.monotonic(), "asr_final_received")

    def check_started(self, turn):
        if self.pending.get(self.active_id) is turn:
            self.deadlines.checking()

    def check_finished(self, turn):
        if self.pending.get(self.active_id) is turn and self.deadlines.accepts(self.active_id):
            self.deadlines.current.mark("lookup_finished", time.monotonic())

    def record_speech(self, turn, piece):
        self.speech_parts.append((piece, (self.active_id, getattr(turn, "speech_phase", "ordinary"))))

    def consume_speech(self, text, *, request_id=None):
        result = []
        while text:
            if not self.speech_parts:
                raise ValueError("TTS text has no core provenance")
            piece, tag = self.speech_parts.popleft()
            if request_id is not None and tag[0] != request_id:
                raise asyncio.CancelledError
            if not self.deadlines.accepts(tag[0]):
                raise asyncio.CancelledError
            size = min(len(text), len(piece))
            if text[:size] != piece[:size]:
                raise ValueError("TTS text differs from the shared core")
            result.append((text[:size], tag))
            text = text[size:]
            if size < len(piece):
                self.speech_parts.appendleft((piece[size:], tag))
        return result

    async def expire(self, timed):
        if self.active_id != timed.request_id:
            return
        turn = self.pending.get(timed.request_id)
        if turn is not None:
            turn.timed_out = True
            task = getattr(turn, "generation_task", None)
            if task is not None and not task.done():
                task.cancel()
        if self.runtime.lookup:
            self.runtime.lookup.cancel_pending()
        if self.status:
            self.status("lookup_timeout" if timed.lookup else "response_timeout", timed.request_id)
        try:
            # Interrupt generation, TTS and the queued audio together. A failed
            # transport cleanup is fatal; an ordinary deadline is turn-local.
            await asyncio.wait_for(asyncio.shield(self.session.interrupt(force=True)), 2)
            timed.mark("interruption_completed", time.monotonic())
        except Exception:
            self.fail()
        finally:
            if self.active_id == timed.request_id:
                self.settle_unconfirmed()

    def playback_finished(self, event):
        if self.active_id in self.playback:
            self.playback[self.active_id].append(event)

    def conversation_item_added(self, event):
        item = event.item
        if getattr(item, "role", None) == "user" and item.text_content:
            # Some STT providers commit several final fragments as one item.
            # Preserve a matching ASR anchor, otherwise use this confirmed item.
            if not self.confirmed or self.confirmed[0] != item.text_content:
                self.confirmed = (item.text_content, time.monotonic(), "user_item_confirmed")
            return
        if (getattr(item, "role", None) != "assistant" or item.id in self.seen_items
                or self.active_id not in self.pending):
            return
        self.seen_items.add(item.id)
        request_id = self.active_id
        turn = self.pending.pop(request_id)
        events = self.playback.pop(request_id, [])
        text = item.text_content or ""
        full_playback = bool(events) and all(not e.interrupted and e.playback_position > 0 for e in events)
        complete = (not item.interrupted and full_playback and turn.generation_complete
                    and text == "".join(turn.generated) and not self.failed and not getattr(turn, "timed_out", False))
        if full_playback:
            delivered = text
        elif events and all(e.synchronized_transcript is not None for e in events):
            delivered = "".join(e.synchronized_transcript for e in events)
        else:
            delivered = UNCERTAIN_DELIVERY
        self.runtime.delivered(turn, delivered, complete=complete)
        self.last_complete = complete
        self.active_id = None
        self.deadlines.finish()

    def settle_unconfirmed(self):
        self.deadlines.finish()
        for turn in self.pending.values():
            # No SDK item means no text delivery evidence. A conservative marker
            # is useful on redial, while all generated words remain unpersisted.
            self.runtime.delivered(turn, UNCERTAIN_DELIVERY, complete=False)
            self.last_complete = False
        self.pending.clear()
        self.playback.clear()
        self.active_id = None

    def fail(self):
        self.failed = True
        if self.session:
            self.session.shutdown(drain=False)

    def bind(self, session):
        self.session = session
        session.on("conversation_item_added", self.conversation_item_added)
        session.on("error", lambda event: self.fail())
        session.on("user_input_transcribed", self.transcribed)


class HerVoiceLLM(llm.LLM):
    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge

    @property
    def model(self):
        return self.bridge.runtime.provider.model

    @property
    def provider(self):
        return "angeleno-conversation"

    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS, **kwargs):
        # SDK context is not an actor history; only the newly completed user
        # utterance crosses this boundary. Her compiles the request exactly once.
        messages = [item for item in chat_ctx.items if isinstance(item, llm.ChatMessage) and item.role == "user"]
        if not messages or not messages[-1].text_content:
            raise ValueError("Her voice requires a user utterance")
        return HerVoiceStream(self, chat_ctx, messages[-1].text_content,
                              replace(conn_options or DEFAULT_API_CONNECT_OPTIONS, max_retry=0))


class HerVoiceStream(llm.LLMStream):
    def __init__(self, model, chat_ctx, user, conn_options):
        super().__init__(llm=model, chat_ctx=chat_ctx, tools=[], conn_options=conn_options)
        self.user = user

    async def _run(self):
        bridge = self._llm.bridge
        request_id = utils.shortuuid("her_")
        iterator = None
        try:
            turn = bridge.begin(request_id, self.user)
            turn.generation_task = asyncio.current_task()
            # An already expired confirmation must not start a model request.
            await asyncio.sleep(0)
            if not bridge.deadlines.allows_audio(request_id):
                raise asyncio.CancelledError
            iterator = bridge.runtime.stream(turn)
            async for piece in iterator:
                bridge.record_speech(turn, piece)
                self._event_ch.send_nowait(llm.ChatChunk(
                    id=request_id, delta=llm.ChoiceDelta(role="assistant", content=piece)))
            if turn.generation_complete:
                bridge.deadlines.generation_done(request_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            bridge.fail()
            raise
        finally:
            if iterator is not None:
                await iterator.aclose()


async def start_voice_session(ctx, agent_factory, env):
    runtime = create_voice_runtime(env)
    cards = None
    statuses = None
    from .voice_cards import VoiceCards, VoiceStatus
    try:
        identity = json.loads(ctx.job.metadata)["user_identity"]
        if not isinstance(identity, str) or not re.fullmatch(r"user-[0-9a-f]{16}", identity):
            raise ValueError("Missing status recipient")
        statuses = VoiceStatus(ctx.room.local_participant, identity, runtime.conversation.session)
        if runtime.lookup is not None:
            cards = VoiceCards(runtime.lookup, ctx.room.local_participant, identity)
    except BaseException:
        if statuses is not None:
            await statuses.close()
        await runtime.close(flush=False)
        raise
    bridge = VoiceBridge(runtime)
    session = AgentSession(tts_text_transforms=None, turn_handling={
        "interruption": {"enabled": True}, "preemptive_generation": {"enabled": False},
    })
    bridge.bind(session)
    session.userdata = {"angeleno_timing": bridge.deadlines.evidence}
    if cards is not None:
        cards.on_error = bridge.fail
    bridge.status = statuses.offer
    statuses.on_error = bridge.fail
    cleanup_task = None
    agent = None

    async def cleanup():
        try:
            await statuses.close()
            if cards is not None:
                await cards.close()
            await session.aclose()  # cancels generation and clears playout first
        finally:
            try:
                # AgentSession stops streams but does not close model owners.
                # This job's VoiceAgent owns the cached HTTP session in its TTS.
                voice_tts = getattr(agent, "voice_tts", None)
                if voice_tts is not None:
                    await voice_tts.aclose()
            finally:
                bridge.settle_unconfirmed()
                await bridge.deadlines.close()
                await runtime.close(flush=not bridge.failed and bridge.last_complete, timeout=5)

    async def finish(reason=None):
        nonlocal cleanup_task
        if cleanup_task is None:
            cleanup_task = asyncio.create_task(cleanup())
        await asyncio.shield(cleanup_task)

    async def after_close():
        await finish()
        ctx.shutdown(reason="Angeleno conversation ended")

    @session.on("close")
    def closed(event):
        # Retained by the job callback; job shutdown cannot abandon maintenance
        # cleanup or leave the identity's database lock held until process exit.
        asyncio.create_task(after_close())

    ctx.add_shutdown_callback(finish)
    try:
        agent = agent_factory("", llm_instance=HerVoiceLLM(bridge))
        await session.start(agent=agent, room=ctx.room)
        install_phase_audio(session, bridge)
    except BaseException:
        bridge.failed = True
        await finish()
        raise
    return session
