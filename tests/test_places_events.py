"""Independent result events, cancellation, and speech separation."""

from dataclasses import replace
from datetime import date, datetime, timezone
import json
import unittest
from pathlib import Path

from angeleno.places import FixedClock, QueryIntent, ResultChannel, load_bundle
from angeleno.places.check import Checker


ROOT = Path(__file__).resolve().parents[1] / "data" / "places"


def bundle():
    return load_bundle(ROOT)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.bundle = bundle()
        self.clock = FixedClock(datetime(2026, 10, 30, 22, 4, 5, tzinfo=timezone.utc))
        self.channel = ResultChannel()
        self.checker = Checker(
            self.bundle.catalog, self.bundle.registry, self.bundle.library, self.clock,
            channel=self.channel,
        )

    def open_fisher(self, *, session="session-a", turn=1, utterance="周六下午能去 Fisher 吗",
                    question="周六", applicable=date(2026, 10, 31)):
        return self.channel.open(
            session_id=session, user_turn=turn, utterance=utterance, question=question,
            place_id="place.fisher", source_id="src.fisher.visit", question_type="hours",
            applicable_date=applicable,
        )

    def test_result_stays_off_the_speech_channel(self):
        intent = self.open_fisher()
        checking = self.channel.card(intent.session_id)
        self.assertEqual(checking.phase, "checking")
        self.assertIsNone(checking.conclusion)
        event = self.checker.check(intent)
        outcome = self.channel.publish(event)
        self.assertTrue(outcome.accepted)
        self.assertEqual(outcome.card.conclusion, event.conclusion)
        self.assertIsNone(event.speech_text())
        body = event.to_dict()
        self.assertEqual(body["channel"], "source_status")
        self.assertNotIn("tts", body)
        self.assertNotIn("speech", body)
        self.assertEqual(self.channel.speech(intent.session_id), "")
        self.channel.append_speech(intent.session_id, "周六他们照常开到傍晚。")
        self.assertNotIn("source_status", self.channel.speech(intent.session_id))
        with self.assertRaises(Exception):
            self.channel.append_speech(intent.session_id, json.dumps(body, ensure_ascii=False))
        with self.assertRaises(Exception):
            self.channel.append_speech(intent.session_id, f"请求 {event.request_id} 已完成")
        self.assertNotIn(event.request_id, self.channel.speech(intent.session_id))

    def test_supported_without_evidence_cannot_become_the_card(self):
        intent = self.open_fisher()
        event = self.checker.check(intent)
        empty = replace(event, citations=())
        outcome = self.channel.publish(empty)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "missing_evidence")
        self.assertIsNone(self.channel.card(intent.session_id).conclusion)
        self.assertEqual(self.channel.card(intent.session_id).phase, "checking")

    def test_cancel_drops_a_late_conclusion(self):
        intent = self.open_fisher()
        event = self.checker.check(intent)
        self.assertEqual(event.status, "supported")
        self.assertTrue(event.conclusion)
        self.channel.cancel(intent.lineage_id)
        card = self.channel.card(intent.session_id)
        self.assertEqual(card.status, "cancelled")
        self.assertIsNone(card.conclusion)
        outcome = self.channel.publish(event)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "cancelled")
        self.assertIsNone(self.channel.card(intent.session_id).conclusion)
        self.assertEqual(self.channel.speech(intent.session_id), "")
        self.assertNotIn(event.conclusion, json.dumps(
            self.channel.card(intent.session_id).__dict__, ensure_ascii=False,
        ))

    def test_already_cancelled_request_is_not_read(self):
        intent = self.open_fisher()
        self.channel.cancel(intent.lineage_id)
        before = self.bundle.library.reads
        event = self.checker.check(intent)
        self.assertEqual(event.status, "cancelled")
        self.assertIsNone(event.conclusion)
        self.assertEqual(self.bundle.library.reads, before)

    def test_revision_keeps_the_utterance_and_retires_the_old_result(self):
        intent = self.open_fisher(utterance="我是说周六，不是别的日子")
        old = self.checker.check(intent)
        revised = self.channel.revise(
            intent, question="下周六", applicable_date=date(2026, 11, 7), user_turn=2,
        )
        self.assertEqual(revised.utterance, "我是说周六，不是别的日子")
        self.assertEqual(revised.supersedes, intent.request_id)
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.user_turn, 2)
        self.assertTrue(self.channel.is_cancelled(intent.request_id))
        self.assertFalse(self.channel.is_cancelled(revised.request_id))
        before = self.bundle.library.reads
        stale = self.checker.check(intent)
        self.assertEqual(stale.status, "cancelled")
        self.assertEqual(self.bundle.library.reads, before)
        fresh = self.checker.check(revised)
        outcome = self.channel.publish(fresh)
        self.assertTrue(outcome.accepted)
        self.assertEqual(outcome.card.applicable_date, "2026-11-07")
        self.assertEqual(outcome.card.request_id, revised.request_id)
        rejected = self.channel.publish(old)
        self.assertFalse(rejected.accepted)
        self.assertEqual(self.channel.card(intent.session_id).applicable_date, "2026-11-07")
        wrong_turn = replace(fresh, user_turn=9)
        self.assertFalse(self.channel.publish(wrong_turn).accepted)

    def test_new_topic_replaces_the_card_without_adopting_the_old_conclusion(self):
        intent = self.open_fisher()
        event = self.checker.check(intent)
        self.channel.publish(event)
        previous = event.conclusion
        follow = self.channel.open(
            session_id=intent.session_id, user_turn=2, utterance="算了，先聊点别的",
            question="先聊点别的", question_type="background",
        )
        card = self.channel.card(intent.session_id)
        self.assertEqual(card.phase, "checking")
        self.assertEqual(card.subject, "先聊点别的")
        self.assertIsNone(card.conclusion)
        self.assertNotEqual(card.lineage_id, intent.lineage_id)
        self.assertFalse(self.channel.publish(event).accepted)
        self.assertIsNone(self.channel.card(intent.session_id).conclusion)
        self.assertNotIn(previous, self.channel.speech(intent.session_id))
        self.assertNotEqual(follow.utterance, intent.utterance)

    def test_sessions_do_not_share_a_card(self):
        first = self.open_fisher()
        event = self.checker.check(first)
        self.channel.publish(event)
        second = self.open_fisher(session="session-b", utterance="另一个会话", question="周日")
        self.assertEqual(self.channel.card("session-a").conclusion, event.conclusion)
        self.assertIsNone(self.channel.card("session-b").conclusion)
        self.assertEqual(self.channel.card("session-b").session_id, "session-b")
        self.channel.append_speech("session-a", "只在这一通里说。")
        self.assertEqual(self.channel.speech("session-b"), "")
        self.assertNotEqual(second.session_id, first.session_id)

    def test_direct_intent_without_a_channel_still_checks(self):
        direct = QueryIntent(
            "loose", 1, "rq-loose", 1, "ln-loose", "原话", "问题", "place.nhm",
            "src.nhm.visit", "hours", date(2026, 10, 31), None, None,
        )
        event = self.checker.check(direct)
        self.assertEqual(event.status, "conflict")
        self.assertIsNone(event.conclusion)
        self.assertEqual(self.channel.card("loose"), None)


if __name__ == "__main__":
    unittest.main()
