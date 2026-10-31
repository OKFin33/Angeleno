"""Result channel, one current card, and cancellation.

Events stay on this channel. Publishing never appends speech. A cancelled or
superseded request cannot update the card, including when a late result still
carries a conclusion.
"""

from dataclasses import dataclass
import json

from .model import CHANNEL, MAX_QUESTION_CHARS, STATUSES, PlacesError, QueryIntent


@dataclass(frozen=True)
class Card:
    session_id: str
    lineage_id: str
    request_id: str
    phase: str
    status: str | None
    subject: str
    conclusion: str | None
    applicable_date: str | None
    checked_at: str | None
    citations: tuple
    conflicts: tuple


@dataclass(frozen=True)
class PublishOutcome:
    accepted: bool
    reason: str | None
    card: Card | None


class ResultChannel:
    def __init__(self, *, on_card=None):
        self.on_card = on_card
        self._lineages = {}
        self._requests = {}
        self._cards = {}
        self._speech = {}
        self._seq = 0
        self.events = []
        self.rejected = []

    def open(self, *, session_id, user_turn, utterance, question, place_id=None,
             source_id=None, question_type, applicable_date=None, stated_zone=None):
        self._require_binding(session_id, user_turn, utterance, question)
        self._supersede_session(session_id)
        lineage_id = self._id("ln")
        request_id = self._id("rq")
        intent = QueryIntent(
            session_id=session_id, user_turn=user_turn, request_id=request_id, version=1,
            lineage_id=lineage_id, utterance=utterance, question=question, place_id=place_id,
            source_id=source_id, question_type=question_type, applicable_date=applicable_date,
            stated_zone=stated_zone, supersedes=None,
        )
        self._store(intent)
        self._cards[session_id] = self._checking_card(intent)
        self._notify(session_id)
        return intent

    def revise(self, intent, *, question, applicable_date, user_turn=None):
        lineage = self._lineages[intent.lineage_id]
        if lineage.utterance != intent.utterance:
            raise PlacesError("original utterance is immutable")
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
            raise PlacesError("question required")
        turn = intent.user_turn if user_turn is None else user_turn
        if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
            raise PlacesError("user_turn required")
        self._cancel_request(intent.request_id)
        revised = QueryIntent(
            session_id=intent.session_id, user_turn=turn, request_id=self._id("rq"),
            version=intent.version + 1, lineage_id=intent.lineage_id, utterance=lineage.utterance,
            question=question, place_id=intent.place_id, source_id=intent.source_id,
            question_type=intent.question_type, applicable_date=applicable_date,
            stated_zone=intent.stated_zone, supersedes=intent.request_id,
        )
        self._store(revised)
        self._cards[intent.session_id] = self._checking_card(revised)
        self._notify(intent.session_id)
        return revised

    def cancel(self, lineage_id):
        lineage = self._lineages[lineage_id]
        lineage.cancelled = True
        current = None
        for request in lineage.requests:
            request.cancelled = True
            current = request.intent
        event = self._cancelled_event(current)
        self.events.append(event)
        card = self._cards.get(lineage.session_id)
        if card is not None and card.lineage_id == lineage_id:
            self._cards[lineage.session_id] = Card(
                session_id=lineage.session_id, lineage_id=lineage_id, request_id=current.request_id,
                phase="result", status="cancelled", subject=card.subject, conclusion=None,
                applicable_date=None, checked_at=None, citations=(), conflicts=(),
            )
            self._notify(lineage.session_id)
        return event

    def publish(self, event):
        reason = self._reject_reason(event)
        if reason is not None:
            self.rejected.append((reason, event))
            return PublishOutcome(False, reason, self._cards.get(event.session_id))
        if event.status in {"supported", "conflict"} and not event.citations:
            self.rejected.append(("missing_evidence", event))
            return PublishOutcome(False, "missing_evidence", self._cards.get(event.session_id))
        if event.status == "supported" and not any(item.excerpt.strip() for item in event.citations):
            self.rejected.append(("missing_evidence", event))
            return PublishOutcome(False, "missing_evidence", self._cards.get(event.session_id))
        if event.status == "conflict" and len(event.conflicts) < 2:
            self.rejected.append(("missing_conflict", event))
            return PublishOutcome(False, "missing_conflict", self._cards.get(event.session_id))
        card = Card(
            session_id=event.session_id, lineage_id=event.lineage_id, request_id=event.request_id,
            phase="result", status=event.status, subject=event.subject, conclusion=event.conclusion,
            applicable_date=event.applicable_date, checked_at=event.checked_at,
            citations=event.citations, conflicts=event.conflicts,
        )
        self._cards[event.session_id] = card
        self.events.append(event)
        self._notify(event.session_id)
        return PublishOutcome(True, None, card)

    def _notify(self, session_id):
        if self.on_card is not None:
            self.on_card(self._cards[session_id])

    def card(self, session_id):
        return self._cards.get(session_id)

    def speech(self, session_id):
        return self._speech.get(session_id, "")

    def append_speech(self, session_id, text):
        if not isinstance(text, str):
            raise PlacesError("speech must be text")
        if CHANNEL in text:
            raise PlacesError("source event mixed into speech")
        for event in self.events:
            if event.session_id != session_id:
                continue
            if event.request_id and event.request_id in text:
                raise PlacesError("source event mixed into speech")
            if json.dumps(event.to_dict(), ensure_ascii=False) in text:
                raise PlacesError("source event mixed into speech")
        self._speech[session_id] = self._speech.get(session_id, "") + text

    def is_cancelled(self, request_id):
        request = self._requests.get(request_id)
        if request is None:
            return False
        return request.cancelled or self._lineages[request.intent.lineage_id].cancelled

    def _reject_reason(self, event):
        if event.channel != CHANNEL or event.speech_text() is not None:
            return "channel"
        if event.status not in STATUSES or event.status == "cancelled":
            return "status"
        request = self._requests.get(event.request_id)
        if request is None:
            return "unknown_request"
        intent = request.intent
        if request.cancelled or self._lineages[intent.lineage_id].cancelled:
            return "cancelled"
        if intent.version != event.version or intent.lineage_id != event.lineage_id:
            return "stale"
        if intent.session_id != event.session_id or intent.user_turn != event.user_turn:
            return "binding"
        card = self._cards.get(event.session_id)
        if card is None or card.lineage_id != event.lineage_id or card.request_id != event.request_id:
            return "stale_lineage"
        return None

    def _supersede_session(self, session_id):
        for lineage in self._lineages.values():
            if lineage.session_id == session_id and not lineage.cancelled:
                lineage.cancelled = True
                for request in lineage.requests:
                    request.cancelled = True

    def _cancel_request(self, request_id):
        request = self._requests[request_id]
        request.cancelled = True

    def _store(self, intent):
        lineage = self._lineages.get(intent.lineage_id)
        if lineage is None:
            lineage = _Lineage(intent.lineage_id, intent.session_id, intent.utterance)
            self._lineages[intent.lineage_id] = lineage
        elif lineage.utterance != intent.utterance:
            raise PlacesError("original utterance is immutable")
        request = _Request(intent)
        lineage.requests.append(request)
        self._requests[intent.request_id] = request

    def _checking_card(self, intent):
        return Card(
            session_id=intent.session_id, lineage_id=intent.lineage_id, request_id=intent.request_id,
            phase="checking", status=None, subject=intent.question, conclusion=None,
            applicable_date=None if intent.applicable_date is None else intent.applicable_date.isoformat(),
            checked_at=None, citations=(), conflicts=(),
        )

    def _cancelled_event(self, intent):
        from .model import ResultEvent
        return ResultEvent(
            channel=CHANNEL, session_id=intent.session_id, user_turn=intent.user_turn,
            request_id=intent.request_id, version=intent.version, lineage_id=intent.lineage_id,
            status="cancelled", subject=intent.question, conclusion=None,
            applicable_date=None, applicable_time=None, checked_at=None, page_updated_at=None,
            citations=(), discarded=(), conflicts=(), refused_navigations=(), ignored_blocks=0,
            incomplete=False, detail="cancelled",
        )

    def _id(self, prefix):
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _require_binding(self, session_id, user_turn, utterance, question):
        if not isinstance(session_id, str) or not session_id:
            raise PlacesError("session_id required")
        if not isinstance(user_turn, int) or isinstance(user_turn, bool) or user_turn < 1:
            raise PlacesError("user_turn required")
        if not isinstance(utterance, str) or not utterance.strip():
            raise PlacesError("utterance required")
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
            raise PlacesError("question required")


class _Lineage:
    def __init__(self, lineage_id, session_id, utterance):
        self.lineage_id = lineage_id
        self.session_id = session_id
        self.utterance = utterance
        self.requests = []
        self.cancelled = False


class _Request:
    def __init__(self, intent):
        self.intent = intent
        self.cancelled = False
