"""Shared conversation lifecycle. Generated text is never delivery evidence."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import re
import uuid
from . import trace as tracing

from .continuity import Conversation, Maintainer
from .memory import MemoryStore, seed_digest
from .language import configured_language, requested_language
from .paths import ROOT


def _user_utterance(turn):
    for message in reversed(turn.messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str) and message["content"].strip():
            return message["content"]
    raise ValueError("A nonempty user utterance is required")


def open_store(root, pack, user, *, create=False, data_dir=None, purpose=None):
    if not user or not re.fullmatch(r"[a-z0-9_-]{1,64}", user):
        raise ValueError("Memory identity must use 1–64 lowercase letters, digits, _ or -")
    root = Path(root)
    from .config import resolve_path
    directory = resolve_path(data_dir or "private/state", root=root)
    path = directory / pack.id / user / "memory.sqlite3"
    if not path.resolve().is_relative_to(directory):
        raise ValueError("Memory path must remain inside the configured data directory")
    if not create and not path.exists():
        raise ValueError("No memory exists for this character/user")
    return MemoryStore(path, character=pack.id, user=user, seed=seed_digest(pack), purpose=purpose)


def non_action_opening(text):
    """A narrow phatic-input gate, not a general natural-language intent parser.

    A greeting/ack alone cannot authorize a task inherited from another call.
    Additional substantive words fall through to ordinary autonomous behavior.
    """
    import unicodedata
    value = ''.join(c for c in text.casefold() if not c.isspace() and unicodedata.category(c)[0] not in {'P','S'})
    phatic = r'(?:hello|hi|hey|ya|yeah|yes|okay|ok|你好|您好|嗨|嘿|欸|诶|嗯哼|嗯|啊|呀|好|行|对|是的|你在吗|在吗|我刚忙完|刚忙完|我回来了|陪我说两句|陪我聊聊|随便聊聊)'
    return bool(value and re.fullmatch(phatic+'+',value))


@dataclass
class Turn:
    messages: list
    temporary: bool = False
    suppress_old_tools: bool = False
    user_seq: int | None = None
    generated: list = field(default_factory=list)
    generation_complete: bool = False
    settled: bool = False


class ConversationRuntime:
    """One compiler, history and tool loop for text and audio adapters.

    Adapters own I/O, cancellation and the evidence of display/playout. Providers
    are injected so tests and bounded trial runners use this exact lifecycle.
    """

    def __init__(self, pack, provider, *, store=None, writer=None, variant="guided",
                 memory_tools=False, lookup=None, attribution_check=False, language=None, trace_env=None, trace_root=None):
        if type(attribution_check) is not bool or (attribution_check and store is None):
            raise ValueError('Attribution check requires an explicit boolean and persistent memory')
        if memory_tools and store is None:
            raise ValueError("Memory tools require a persistent identity")
        if store is not None and writer is None:
            raise ValueError("Persistent conversation requires a maintenance provider")
        self.language = configured_language() if language is None else configured_language({"ANGELENO_LANGUAGE": language})
        self.pack, self.provider, self.store, self.writer = pack, provider, store, writer
        self.variant, self.memory_tools = variant, memory_tools
        self.lookup = lookup
        self.voice_output = False  # Set only by the voice adapter, never user text.
        self._lookup_turns = 0
        self.conversation = Conversation(pack, store, variant=variant, language=self.language) if store else None
        self.maintainer = Maintainer(store, pack, writer, attribution_check=attribution_check, language=self.language) if store else None
        self.history, self.temporary_history = [], []
        self.temporary = False
        self.completed = 0
        self.active = None
        self.closed = False
        self._current_call_topic = False
        self.trace = tracing.Trace(trace_env, root=trace_root or ROOT,
                                   purpose=getattr(store, 'purpose', None))
        self.trace_session = self.conversation.session if self.conversation else uuid.uuid4().hex
        self.trace_binding = tracing.Binding(self.trace, self.trace_session)
        self.trace_turns = 0
        tracing.record('session', binding=self.trace_binding, code_version=self.trace.version, working_tree_dirty=self.trace.dirty,
                       character=pack.id, role=getattr(pack,'role','character'), author=pack.provenance(),
                       configured_language=self.language, memory=store is not None,
                       memory_search=memory_tools, lookup=type(lookup).__name__ if lookup is not None else None)

    def set_temporary(self, enabled):
        self.temporary = enabled
        self.temporary_history = []

    def begin(self, user):
        if self.closed:
            raise ValueError("Conversation is closed")
        if self.active and not self.active.settled:
            raise ValueError("Previous turn has no delivery disposition")
        if not isinstance(user, str) or not user.strip():
            raise ValueError("A nonempty user utterance is required")
        self.language = requested_language(user, self.language)
        if self.conversation:
            self.conversation.language = self.language
        if self.maintainer:
            self.maintainer.language = self.language
        if self.store and (any(word in user for word in ("别记", "不要记", "别保存", "不要保存"))
                           or re.search(r"(?:^|[.!?]\s+)(?:please[ ,]+)?(?:do not|don't|don’t) (?:save|store|remember) (?:this|our|anything|that)\b", user, re.I)):
            self.set_temporary(True)
        if self.conversation and self.temporary:
            self.temporary_history.append({"role": "user", "content": user})
            messages = self.conversation.temporary(self.temporary_history)
        elif self.conversation:
            messages = self.conversation.begin(user)
        else:
            messages = self.pack.messages([*self.history, {"role": "user", "content": user}], self.variant, language=self.language)
        phatic = non_action_opening(user)
        suppress_old_tools = not self._current_call_topic and phatic
        if not phatic:
            self._current_call_topic = True
        if suppress_old_tools:
            messages[0]["content"] += "\n[本次通话输入边界]对方目前只在寒暄或附和，尚未在这次通话提出或明确续接旧任务。旧任务记录是历史，不是待执行指令；自然接话，必要时澄清，不能声称已经新查证。"
        turn = Turn(messages, temporary=self.temporary, suppress_old_tools=suppress_old_tools,
                    user_seq=self.store.current_turn(self.conversation.session)
                    if self.store and not self.temporary else None)
        turn.language = self.language
        self.trace_turns += 1
        turn.trace_binding = tracing.Binding(self.trace, self.trace_session, 't'+str(self.trace_turns), not turn.temporary)
        if self.maintainer is not None:self.maintainer.trace_binding=turn.trace_binding
        tracing.record('turn_input', binding=turn.trace_binding, text=user, language=self.language,
                       temporary=turn.temporary, user_seq=turn.user_seq,
                       tools_suppressed=suppress_old_tools,
                       observable_reason='new_call_phatic_no_old_task' if suppress_old_tools else None)
        self.active = turn
        return turn

    async def stream(self, turn):
        iterator=self._stream(turn)
        status='interrupted'
        with tracing.activate(getattr(turn,'trace_binding',None)):
            try:
                async for piece in iterator:yield piece
                status='completed'
            except asyncio.CancelledError:
                status='cancelled';raise
            except Exception as exc:
                status='error'
                tracing.record('generation_error', error_type=type(exc).__name__)
                raise
            finally:
                try:await iterator.aclose()
                finally:tracing.record('generation_end', status=status, text=''.join(turn.generated),
                                      complete=turn.generation_complete)

    async def _stream(self, turn):
        if turn is not self.active or turn.settled or self.closed:
            raise ValueError("Inactive conversation turn")
        if turn.suppress_old_tools:
            iterator = self.provider.chat(turn.messages)
        elif self.lookup is not None:
            from .lookup import reply as lookup_reply
            utterance = _user_utterance(turn)
            if self.conversation is not None:
                session_id = self.conversation.session
            else:
                session_id = self.lookup.local_session
            if turn.user_seq is not None:
                user_turn = turn.user_seq
            else:
                self._lookup_turns += 1
                user_turn = self._lookup_turns
            iterator = lookup_reply(
                self.lookup, self.provider, turn.messages, session_id=session_id,
                user_turn=user_turn, utterance=utterance, turn=turn,
                store=self.store if self.memory_tools else None, user_seq=turn.user_seq,
                voice_output=self.voice_output,
            )
        elif self.memory_tools:
            from .memory_tools import reply
            iterator = reply(self.provider, turn.messages, self.store, user_seq=turn.user_seq)
        else:
            iterator = self.provider.chat(turn.messages)
        try:
            async for piece in iterator:
                if not isinstance(piece, str):
                    raise ValueError("Only spoken text may leave the conversation core")
                if piece:
                    turn.generated.append(piece)
                    yield piece
            if not turn.generated:
                raise ValueError("Provider returned no spoken text")
            turn.generation_complete = True
        finally:
            await iterator.aclose()
            if self.lookup is not None and not turn.generation_complete:
                self.lookup.cancel_pending()

    def delivered(self, turn, text, *, complete=False):
        if turn.settled:
            return
        if complete and (not turn.generation_complete or text != "".join(turn.generated)):
            raise ValueError("Complete delivery must match a completed generation")
        # text comes from an adapter's display/playout evidence, never this
        # class's generated buffer. Empty text records only the user's input.
        if self.conversation and not turn.temporary:
            self.conversation.delivered(text, complete=complete)
        elif turn.temporary:
            if text:
                self.temporary_history = [*self.temporary_history,
                                          {"role": "assistant", "content": text}][-20:]
        elif text:
            self.history = [*turn.messages[1:], {"role": "assistant", "content": text}]
        if (self.lookup is not None and not turn.suppress_old_tools and self.store is not None and complete and text
                and not turn.temporary):
            self.lookup.record_if_delivered(
                self.store, getattr(turn, "lookup_session", None),
                getattr(turn, "lookup_user_turn", None), text,
            )
        tracing.record('delivery', binding=getattr(turn,'trace_binding',None), text=text, complete=complete,
                       evidence='adapter_display_or_server_playout_not_human_hearing', temporary=turn.temporary)
        turn.settled = True
        if complete and not turn.temporary:
            self.completed += 1
            if self.maintainer and self.completed % 3 == 0:
                self.maintainer.schedule()
        if self.store is not None and self.trace.path is not None:
            try:
                tracing.record('memory_state', binding=turn.trace_binding, revision=self.store.revision,
                    watermark=self.store.watermark, pending_events=len(self.store.pending()),
                    maintenance_error=self.maintainer.error,
                    maintenance_scheduled=self.maintainer.task is not None and not self.maintainer.task.done())
            except Exception:pass

    async def close(self, *, flush=True, timeout=None):
        if self.closed:
            return
        self.closed = True
        tracing.record('session_closing', binding=self.trace_binding, completed_turns=self.completed)
        try:
            if self.maintainer:
                if flush and self.completed and (self.active is None or self.active.settled):
                    try:
                        if timeout is None:
                            await self.maintainer.flush()
                        else:
                            await asyncio.wait_for(self.maintainer.flush(), timeout)
                    except TimeoutError:
                        self.maintainer.error = "TimeoutError"
                        await self.maintainer.cancel()
                else:
                    await self.maintainer.cancel()
        finally:
            try:
                async def close_provider(provider):
                    if provider is not None:
                        close = getattr(provider, "aclose", None)
                        if close is not None:
                            await close()
                        elif getattr(provider, "_client", None) is not None:
                            await provider._client.aclose()
                await asyncio.gather(*(close_provider(p) for p in (self.provider, self.writer)),
                                     return_exceptions=True)
            finally:
                if self.store:
                    self.store.close()
                tracing.record('session_closed', binding=self.trace_binding, completed_turns=self.completed)
                tracing.forget(self.trace_session)
