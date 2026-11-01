"""Shared conversation lifecycle. Generated text is never delivery evidence."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import re

from .continuity import Conversation, Maintainer
from .memory import MemoryStore, seed_digest


def _user_utterance(turn):
    for message in reversed(turn.messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str) and message["content"].strip():
            return message["content"]
    raise ValueError("A nonempty user utterance is required")


def open_store(root, pack, user, *, create=False, data_dir=None):
    if not user or not re.fullmatch(r"[a-z0-9_-]{1,64}", user):
        raise ValueError("Memory identity must use 1–64 lowercase letters, digits, _ or -")
    root = Path(root)
    from .config import resolve_path
    directory = resolve_path(data_dir or "private/state", root=root)
    if not directory.is_relative_to((root / "private").resolve()):
        raise ValueError("Memory data directory must remain inside this repository private directory")
    path = directory / pack.id / user / "memory.sqlite3"
    if not path.resolve().is_relative_to(directory):
        raise ValueError("Memory path must remain inside the configured data directory")
    if not create and not path.exists():
        raise ValueError("No memory exists for this character/user")
    return MemoryStore(path, character=pack.id, user=user, seed=seed_digest(pack))


@dataclass
class Turn:
    messages: list
    temporary: bool = False
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
                 memory_tools=False, lookup=None, attribution_check=False):
        if type(attribution_check) is not bool or (attribution_check and store is None):
            raise ValueError('Attribution check requires an explicit boolean and persistent memory')
        if memory_tools and store is None:
            raise ValueError("Memory tools require a persistent identity")
        if store is not None and writer is None:
            raise ValueError("Persistent conversation requires a maintenance provider")
        self.pack, self.provider, self.store, self.writer = pack, provider, store, writer
        self.variant, self.memory_tools = variant, memory_tools
        self.lookup = lookup
        self.voice_output = False  # Set only by the voice adapter, never user text.
        self._lookup_turns = 0
        self.conversation = Conversation(pack, store, variant=variant) if store else None
        self.maintainer = Maintainer(store, pack, writer, attribution_check=attribution_check) if store else None
        self.history, self.temporary_history = [], []
        self.temporary = False
        self.completed = 0
        self.active = None
        self.closed = False

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
        if self.store and any(word in user for word in ("别记", "不要记", "别保存", "不要保存")):
            self.set_temporary(True)
        if self.conversation and self.temporary:
            self.temporary_history.append({"role": "user", "content": user})
            messages = self.conversation.temporary(self.temporary_history)
        elif self.conversation:
            messages = self.conversation.begin(user)
        else:
            messages = self.pack.messages([*self.history, {"role": "user", "content": user}], self.variant)
        turn = Turn(messages, temporary=self.temporary,
                    user_seq=self.store.current_turn(self.conversation.session)
                    if self.store and not self.temporary else None)
        self.active = turn
        return turn

    async def stream(self, turn):
        if turn is not self.active or turn.settled or self.closed:
            raise ValueError("Inactive conversation turn")
        if self.lookup is not None:
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
        if (self.lookup is not None and self.store is not None and complete and text
                and not turn.temporary):
            self.lookup.record_if_delivered(
                self.store, getattr(turn, "lookup_session", None),
                getattr(turn, "lookup_user_turn", None), text,
            )
        turn.settled = True
        if complete and not turn.temporary:
            self.completed += 1
            if self.maintainer and self.completed % 3 == 0:
                self.maintainer.schedule()

    async def close(self, *, flush=True, timeout=None):
        if self.closed:
            return
        self.closed = True
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
