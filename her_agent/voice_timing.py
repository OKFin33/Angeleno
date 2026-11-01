"""Turn deadlines and audio provenance. No transcript is playback evidence."""

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
import time

from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.voice import io
from .diagnostics import emit


ORDINARY_SECONDS = 15.0
LOOKUP_SECONDS = 30.0
AUDIO_TAG = "angeleno.turn_audio"


@dataclass
class TimedTurn:
    request_id: str
    confirmed_at: float
    anchor: str
    lookup: bool = False
    first_audio_at: float | None = None
    answer_audio_at: float | None = None
    generation_finished: bool = False
    expired: bool = False
    finished: bool = False
    timeout_task: asyncio.Task | None = None
    events: list = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def mark(self, name, now):
        self.events.append({"event": name, "after_confirmed_ms": round((now - self.confirmed_at) * 1000)})


class TurnDeadlines:
    """One monotonic origin; a waiting prompt never resets either deadline."""

    def __init__(self, expire, *, ordinary=ORDINARY_SECONDS, lookup=LOOKUP_SECONDS):
        self.expire = expire
        self.ordinary, self.lookup = ordinary, lookup
        self.current = None
        self.task = None
        self.tasks = set()
        self.evidence = deque(maxlen=100)

    def begin(self, request_id, *, confirmed_at=None, anchor=None):
        self.finish()
        now = time.monotonic()
        turn = TimedTurn(request_id, now if confirmed_at is None else confirmed_at,
                         anchor or ("generation_start" if confirmed_at is None else "confirmation_received"))
        turn.mark("generation_started", now)
        self.current = turn
        self.evidence.append(turn)
        self.task = asyncio.create_task(self._watch(turn))
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)
        return turn

    def checking(self):
        turn = self.current
        if turn and not turn.finished and not turn.expired:
            turn.lookup = True
            turn.mark("lookup_started", time.monotonic())
            turn.changed.set()

    def audio_started(self, request_id, phase):
        turn = self.current
        if not self.allows_audio(request_id):
            return
        now = time.monotonic()
        if turn.first_audio_at is None:
            turn.first_audio_at = now
            turn.mark("server_audio_started", now)
        if phase == "answer" and turn.answer_audio_at is None:
            turn.answer_audio_at = now
            turn.mark("server_lookup_answer_started", now)
        turn.changed.set()

    def accepts(self, request_id):
        turn = self.current
        return bool(turn and turn.request_id == request_id and not turn.expired and not turn.finished)

    def generation_done(self, request_id):
        if self.accepts(request_id):
            emit("generation_finished", request_id=request_id)
            self.current.generation_finished = True
            self.current.mark("generation_finished", time.monotonic())
            self.current.changed.set()

    def _deadline(self, turn):
        if turn.lookup and turn.answer_audio_at is None:
            return turn.confirmed_at + self.lookup
        if turn.first_audio_at is None:
            return turn.confirmed_at + self.ordinary
        if not turn.generation_finished:
            # An ordinary-sounding prefix can precede a tool call whose name
            # or arguments have not arrived yet. Bound unresolved generation
            # by the existing outer deadline, without guessing the words'
            # meaning or restarting the clock when the tool finally arrives.
            return turn.confirmed_at + self.lookup
        return None

    def allows_audio(self, request_id):
        if not self.accepts(request_id):
            return False
        turn = self.current
        deadline = self._deadline(turn)
        if deadline is not None and time.monotonic() >= deadline:
            self._expire_now(turn)
            return False
        return True

    def _expire_now(self, turn):
        if turn.expired or turn.finished:
            return
        turn.expired = True
        turn.mark("lookup_timeout" if turn.lookup else "response_timeout", time.monotonic())
        turn.changed.set()
        turn.timeout_task = asyncio.create_task(self.expire(turn))
        self.tasks.add(turn.timeout_task)
        turn.timeout_task.add_done_callback(self.tasks.discard)

    def finish(self):
        if self.current and not self.current.finished:
            self.current.finished = True
            self.current.mark("turn_settled", time.monotonic())
            self.current.changed.set()
        # Never cancel the timeout handler from its own settlement callback.
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()

    async def _watch(self, turn):
        while not turn.finished and not turn.expired:
            turn.changed.clear()
            deadline = self._deadline(turn)
            if deadline is None:
                await turn.changed.wait()
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._expire_now(turn)
                return
            try:
                await asyncio.wait_for(turn.changed.wait(), remaining)
            except TimeoutError:
                pass

    async def close(self):
        self.finish()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def tagged_tts(text, bridge, synthesizer):
    """Synthesize sentences with an exact turn/phase tag outside spoken text.

    Phase boundaries use separate streams, so waiting audio cannot hide the
    first answer frame. No guessed word-to-audio alignment is manufactured.
    """
    buffer, tag = "", None
    request_id = None

    async def frames(sentence, current_tag):
        if not bridge.deadlines.accepts(current_tag[0]):
            raise asyncio.CancelledError
        options = replace(DEFAULT_API_CONNECT_OPTIONS, max_retry=0)
        reason = "error"
        try:
            async with synthesizer.synthesize(sentence, conn_options=options) as stream:
                async for event in stream:
                    if not bridge.deadlines.accepts(current_tag[0]):
                        raise asyncio.CancelledError
                    event.frame.userdata[AUDIO_TAG] = current_tag
                    yield event.frame
            reason = "eof"
        except (asyncio.CancelledError, GeneratorExit):
            reason = "cancelled"
            raise
        finally:
            emit("tts_stream_end", request_id=current_tag[0], phase=current_tag[1], reason=reason)

    async for chunk in text:
        for piece, current_tag in bridge.consume_speech(chunk, request_id=request_id):
            request_id = current_tag[0]
            if tag is not None and current_tag != tag and buffer.strip():
                async for frame in frames(buffer, tag):
                    yield frame
                buffer = ""
            tag = current_tag
            buffer += piece
            while True:
                end = next((i + 1 for i, char in enumerate(buffer) if char in "。！？；!?;"), None)
                if end is None:
                    break
                sentence, buffer = buffer[:end], buffer[end:]
                if sentence.strip():
                    async for frame in frames(sentence, tag):
                        yield frame
    if buffer.strip():
        async for frame in frames(buffer, tag):
            yield frame


class PhaseAudioOutput(io.AudioOutput):
    """Split playout at the leaf, preserving one segment for SDK transcription.

    Intermediate drains are private to this adapter. Upstream gets one start,
    cumulative progress and one finish for the whole generated reply.
    """

    def __init__(self, sink, bridge):
        # The session already owns its stable tail proxy. Connect directly to
        # the leaf, so a cancelled pre-accept capture cannot leave another
        # proxy's phantom segment waiting for a playback event that never comes.
        super().__init__(label="angeleno-phase-audio", capabilities=io.AudioOutputCapabilities(pause=sink.can_pause),
                         sample_rate=sink.sample_rate)
        self.sink = sink
        sink.on("playback_started", self._forward_next_playback_started)
        sink.on("playback_finished", self._forward_next_playback_finished)
        sink.on("playback_progressed", self._forward_next_playback_progressed)
        self.bridge = bridge
        self.tag = None
        self.open = False
        self.started = False
        self.draining = False
        self.drained = False
        self.played = 0.0

    @property
    def next_in_chain(self):
        return self.sink

    async def capture_frame(self, frame):
        tag = frame.userdata.get(AUDIO_TAG)
        if not tag or not self.bridge.deadlines.allows_audio(tag[0]):
            raise asyncio.CancelledError
        if self.open and self.tag != tag:
            self.draining = True
            try:
                self.sink.flush()
                event = await self.sink.wait_for_playout()
            finally:
                self.draining = False
            if event.interrupted:
                raise asyncio.CancelledError
            if not self.bridge.deadlines.allows_audio(tag[0]):
                raise asyncio.CancelledError
        if not self.open:
            self.started = False
            self.played = 0.0
        self.tag = tag
        await self.sink.capture_frame(frame)
        # A room output may wait for a subscriber before accepting its first
        # frame. Do not leave a phantom playout segment if that wait is cancelled.
        await super().capture_frame(frame)
        self.open = True
        self.drained = False

    def _forward_next_playback_started(self, event):
        if self.tag is not None:
            self.bridge.deadlines.audio_started(*self.tag)
        if not self.started:
            self.started = True
            super()._forward_next_playback_started(event)

    def _forward_next_playback_progressed(self, event):
        super()._forward_next_playback_progressed(replace(event, offset=self.played + event.offset))

    def _forward_next_playback_finished(self, event):
        if self.draining:
            self.played += event.playback_position
            self.drained = True
            return
        # Partial transcript estimates belong to the upper SDK synchronizer,
        # which sees the complete reply, not an individual leaf phase.
        emit("playout_finished", request_id=self.tag[0], phase=self.tag[1],
             interrupted=event.interrupted or not self.bridge.deadlines.accepts(self.tag[0]))
        super()._forward_next_playback_finished(replace(event,
            playback_position=self.played + event.playback_position, synchronized_transcript=None,
            interrupted=event.interrupted or not self.bridge.deadlines.accepts(self.tag[0])))

    def flush(self):
        super().flush()
        self.open = False
        if self.drained:
            # Cancellation between the private drain and the next accepted
            # frame still owes the upstream a finish for the original reply.
            self.drained = False
            self.on_playback_finished(playback_position=self.played, interrupted=True)
        else:
            self.sink.flush()

    def clear_buffer(self):
        super().flush()
        self.open = False
        self.sink.clear_buffer()


def install_phase_audio(session, bridge):
    """Keep the room's transcript/recorder wrappers above the phase adapter."""
    sink = session.output.audio
    if sink is None:
        raise ValueError("Her voice requires an audio output")
    while sink.next_in_chain is not None:
        sink = sink.next_in_chain
    output = PhaseAudioOutput(sink, bridge)
    session.output.replace_audio_tail(output)
    session.output.audio.on("playback_finished", bridge.playback_finished)
    return output
