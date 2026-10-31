"""One bounded, ordered card channel addressed only to this dial's participant."""

import asyncio
import json

from .lookup import card_public


TOPIC = "angeleno.source"
STATUS_TOPIC = "angeleno.turn_status"


class VoiceStatus:
    """Latest turn status only; no user text or history crosses this channel."""

    def __init__(self, participant, identity, session):
        if not identity or not session:
            raise ValueError("Status recipient and session required")
        self.participant, self.identity, self.session = participant, identity, session
        self.queue = asyncio.Queue(maxsize=1)
        self.sequence = 0
        self.on_error = None
        self.closed = False
        self.task = asyncio.create_task(self._run())

    def offer(self, state, request_id):
        if self.closed:
            return
        self.sequence += 1
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait({"type": STATUS_TOPIC, "recipient": self.identity, "session_id": self.session,
            "sequence": self.sequence, "request_id": request_id, "state": state})

    async def _run(self):
        try:
            while True:
                payload = await self.queue.get()
                await asyncio.wait_for(self.participant.publish_data(json.dumps(payload).encode(),
                    reliable=True, destination_identities=[self.identity], topic=STATUS_TOPIC), 2)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.closed = True
            if self.on_error:
                self.on_error()

    async def close(self):
        self.closed = True
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class VoiceCards:
    def __init__(self, lookup, participant, identity):
        if not identity:
            raise ValueError("Card recipient required; room broadcasts are forbidden")
        self.lookup, self.participant, self.identity = lookup, participant, identity
        self.queue = asyncio.Queue(maxsize=1)
        self.sequence = 0
        self.closed = False
        self.error = None
        self.on_error = None
        lookup.channel.on_card = self.offer
        self.task = asyncio.create_task(self._run())

    def offer(self, card):
        if self.closed:
            return
        self.sequence += 1
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait((self.sequence, card))

    def _payload(self, sequence, card):
        value = card_public(card)
        for citation in value["citations"]:
            source = self.lookup.bundle.registry.get(citation["source_id"])
            if citation["url"] not in source.allowed_urls:
                raise ValueError("Unregistered card link")
            citation["name"] = source.institution
        envelope = {"type": TOPIC, "recipient": self.identity, "sequence": sequence, "card": value}
        payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) > 14000:
            # The full evidence remains in the core. Preserve conclusions,
            # exceptions and registered links; omit optional excerpt display.
            for citation in value["citations"]:
                citation.pop("excerpt", None)
            payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) > 14000:
            raise ValueError("Card exceeds transport budget")
        return payload

    async def _run(self):
        try:
            while True:
                sequence, card = await self.queue.get()
                if self.lookup.channel.card(card.session_id) != card:
                    continue
                await asyncio.wait_for(self.participant.publish_data(
                    self._payload(sequence, card), reliable=True,
                    destination_identities=[self.identity], topic=TOPIC,
                ), timeout=2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = type(exc).__name__
            self.closed = True
            if self.on_error is not None:
                self.on_error()

    async def close(self):
        self.closed = True
        self.lookup.channel.on_card = None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
