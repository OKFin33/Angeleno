"""LiveKit 1.8.4 adapter: SDK playout evidence feeds the shared Her core.

The room transcript is transport state only. It never compiles an actor prompt.
Playback events describe server-side playout, not proof of human attention.
"""

import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path

from livekit.agents import AgentSession, llm, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

from .baseline import configured_pack
from .runtime import ConversationRuntime, open_store
from .provider import configured_providers


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
    for key in ("ANGELENO_MEMORY_CREATE", "ANGELENO_MEMORY_TOOLS", "ANGELENO_PLACE_TOOLS"):
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
        return ConversationRuntime(pack, actor, store=store, writer=writer, variant=variant,
                                   memory_tools=env.get("ANGELENO_MEMORY_TOOLS") == "1",
                                   lookup=live_lookup() if env.get("ANGELENO_PLACE_TOOLS") == "1" else None)
    except BaseException:
        store.close()
        raise


class VoiceBridge:
    """Correlate one serialized SDK generation with its actual playout events."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.pending = {}
        self.playback = {}
        self.active_id = None
        self.session = None
        self.failed = False
        self.last_complete = True
        self.seen_items = set()

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
        return turn

    def playback_finished(self, event):
        if self.active_id in self.playback:
            self.playback[self.active_id].append(event)

    def conversation_item_added(self, event):
        item = event.item
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
                    and text == "".join(turn.generated) and not self.failed)
        if full_playback:
            delivered = text
        elif events and all(e.synchronized_transcript is not None for e in events):
            delivered = "".join(e.synchronized_transcript for e in events)
        else:
            delivered = UNCERTAIN_DELIVERY
        self.runtime.delivered(turn, delivered, complete=complete)
        self.last_complete = complete
        self.active_id = None

    def settle_unconfirmed(self):
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
            iterator = bridge.runtime.stream(turn)
            async for piece in iterator:
                self._event_ch.send_nowait(llm.ChatChunk(
                    id=request_id, delta=llm.ChoiceDelta(role="assistant", content=piece)))
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
    if runtime.lookup is not None:
        from .voice_cards import VoiceCards
        try:
            identity = json.loads(ctx.job.metadata)["user_identity"]
            if not isinstance(identity, str) or not re.fullmatch(r"user-[0-9a-f]{16}", identity):
                raise ValueError("Missing card recipient")
            cards = VoiceCards(runtime.lookup, ctx.room.local_participant, identity)
        except BaseException:
            await runtime.close(flush=False)
            raise
    bridge = VoiceBridge(runtime)
    session = AgentSession(turn_handling={
        "interruption": {"enabled": True}, "preemptive_generation": {"enabled": False},
    })
    bridge.bind(session)
    if cards is not None:
        cards.on_error = bridge.fail
    cleanup_task = None

    async def cleanup():
        try:
            if cards is not None:
                await cards.close()
            await session.aclose()  # cancels generation and clears playout first
        finally:
            bridge.settle_unconfirmed()
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
        await session.start(agent=agent_factory("", llm_instance=HerVoiceLLM(bridge)), room=ctx.room)
        if session.output.audio is None:
            raise ValueError("Her voice requires an audio output")
        session.output.audio.on("playback_finished", bridge.playback_finished)
    except BaseException:
        bridge.failed = True
        await finish()
        raise
    return session
