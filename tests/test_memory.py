"""Synthetic continuity scenarios; no private history or model service is used."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from angeleno.author import load_pack
from angeleno.continuity import Conversation, Maintainer
from angeleno.memory import BacklogFull, MemoryStore, StaleBatch, seed_digest


def record(rid, text, evidence, *, kind="episode", known=True, keywords=None):
    return {"id": rid, "kind": kind, "text": text, "evidence": evidence,
            "known": known, "keywords": keywords or []}


def pack_at(root):
    (root / "character.md").write_text("林是一名成年画框修复师，与用户认识三年。普通兼容往事可增补，职业和初始关系不可覆盖。")
    path = root / "pack.json"
    path.write_text(json.dumps({"format": "her-author-pack/1", "id": "synthetic", "as_of": "2026-10-01",
                               "documents": [{"path": "character.md", "kind": "character"}]}))
    return load_pack(path)


class StoreFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = pack_at(self.root)
        self.path = self.root / "memory.sqlite3"
        self.store = self.open()

    def open(self, **kwargs):
        return MemoryStore(self.path, **{"character": self.pack.id, "user": "test-user",
                                        "seed": seed_digest(self.pack), **kwargs})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def exchange(self, user, assistant="合成的完整回应。", session="old-session"):
        return [self.store.append(session, "user", user), self.store.append(session, "assistant", assistant)]

    def apply(self, *updates, retire=None):
        batch = self.store.batch()
        self.store.apply(batch, {"updates": list(updates), "retire": retire or []})
        return batch

    def archive_recent(self):
        """Move evidence outside the real 20-message window, without model calls."""
        from angeleno.author import MAX_HISTORY_MESSAGES
        for n in range(MAX_HISTORY_MESSAGES // 2):
            self.exchange(f"归档窗口推进{n}。", "收到。", session="archive-padding")
        while self.store.batch():
            self.store.apply(self.store.batch(), {"updates": [], "retire": []})


class MemoryTests(StoreFixture, unittest.TestCase):
    def test_reopen_and_recall_beyond_recent_window_without_loading_whole_history(self):
        ids = self.exchange("我想去 MOCA，但担心看不懂那个展览。", "可以从其中一件作品开始。")
        self.apply(record("episode.moca", "用户想去 MOCA，担心看不懂；她提议先看一件作品。", ids,
                          keywords=["MOCA", "当代艺术馆", "展览"]))
        for i in range(36):
            self.exchange(f"无关测试话题{i}。", session="later-session")
            self.apply()
        self.store.close()
        self.store = self.open()
        chat = Conversation(self.pack, self.store)
        sent = chat.begin("上次聊的当代艺术馆，我去了。")
        self.assertIn("担心看不懂", sent[0]["content"])
        self.assertNotIn("无关测试话题0。", str(sent))
        self.assertIn("无关测试话题35。", str(sent))
        self.assertLessEqual(len(sent), 21)
        self.assertLess(sum(len(m["content"]) for m in sent), 64_000)
        self.assertEqual(sent[-1]["content"], "上次聊的当代艺术馆，我去了。")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_corrected_cards_and_retired_event_replace_old_recall(self):
        ids = self.exchange("展览约在周六。")
        self.apply(record("card.user", "用户展览计划是周六。", ids, kind="user"),
                   record("episode.visit_sat", "展览约在周六。", ids, keywords=["展览"]))
        new = self.exchange("展览时间说错了，是周日。")
        self.apply(record("card.user", "用户纠正展览计划为周日。", new, kind="user"),
                   record("episode.visit_sun", "展览改为周日。", new, keywords=["展览"]), retire=["episode.visit_sat"])
        sent = Conversation(self.pack, self.store).begin("展览是哪天来着？")
        self.assertIn("周日", sent[0]["content"])
        self.assertNotIn("周六", sent[0]["content"].split("以下 JSON")[-1])
        self.assertIn("展览时间说错了，是周日", str(sent))

    def test_pending_correction_survives_until_maintenance_and_across_restart(self):
        ids = self.exchange("展览周六去。")
        self.apply(record("card.active", "展览周六。", ids, kind="active"))
        self.exchange("不对，展览改到周日。", session="second-session")
        self.store.close()
        self.store = self.open()
        sent = Conversation(self.pack, self.store).begin("刚才改成哪天了？")
        self.assertIn("展览改到周日", sent[0]["content"])
        self.assertIn("近期用户纠正优先", sent[0]["content"])

    def test_every_unprocessed_message_survives_the_recent_window_boundary(self):
        chat = Conversation(self.pack, self.store)
        for i in range(10):
            chat.begin(f"USER_MARKER_{i:02d}")
            chat.delivered(f"ASSISTANT_MARKER_{i:02d}")
        sent = chat.begin("第十一轮")
        self.assertEqual(len(self.store.pending()), 21)
        for event in self.store.pending():
            self.assertIn(event["text"], str(sent))

    def test_retired_current_card_can_be_recreated_but_old_episode_cannot(self):
        ids = self.exchange("周六看展。")
        self.apply(record("card.active", "周六看展", ids, kind="active"),
                   record("episode.old_plan", "周六看展", ids, keywords=["展览"]))
        self.exchange("展览已经看完了。")
        self.apply(retire=["card.active", "episode.old_plan"])
        ids = self.exchange("下周讨论鼓谱。")
        watermark = self.store.watermark
        with self.assertRaises(ValueError):
            self.apply(record("episode.old_plan", "周六看展", ids))
        self.assertEqual(self.store.watermark, watermark)
        self.apply(record("card.active", "下周讨论鼓谱", ids, kind="active"))
        sent = Conversation(self.pack, self.store).begin("有什么未完的事？")
        self.assertIn("下周讨论鼓谱", str(sent))
        self.assertNotIn("周六看展", str(self.store.records()))
        self.assertIn("展览已经看完了", str(sent))
        self.assertEqual(self.store.pending()[-1]["role"], "user")

    def test_stale_and_repeated_batch_cannot_overwrite_new_state(self):
        ids = self.exchange("初始记忆。")
        batch = self.apply(record("card.user", "旧认识。", ids, kind="user"))
        for action in ("repeat", "after-forget"):
            if action == "after-forget":
                self.store.forget()
            with self.assertRaises(StaleBatch):
                self.store.apply(batch, {"updates": [record("card.user", "不能恢复旧内容", ids, kind="user")], "retire": []})

    def test_invalid_proposal_is_atomic_and_keeps_pending_evidence(self):
        ids = self.exchange("用户报告的事情。")
        batch = self.store.batch()
        valid = record("card.user", "有效候选", ids, kind="user")
        invalid = record("episode.invalid", "编造的引用", [999])
        with self.assertRaises(ValueError):
            self.store.apply(batch, {"updates": [valid, invalid], "retire": []})
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.watermark, 0)
        self.assertEqual(len(self.store.pending()), 2)

    def test_real_trial_update_and_retire_same_card_remains_an_atomic_rejection(self):
        ids = self.exchange("周六下午有空。")
        self.apply(record("card.user", "周六下午有空", ids, kind="user"))
        new = self.exchange("改成周日；看展只是设想，还没约定。")
        corrected = record("card.user", "周日下午有空，看展仅为设想", new, kind="user")
        before, watermark = self.store.records(), self.store.watermark
        with self.assertRaises(ValueError):
            self.apply(corrected, retire=["card.user"])
        self.assertEqual(self.store.records(), before)
        self.assertEqual(self.store.watermark, watermark)
        self.assertEqual(len(self.store.pending()), 2)
        self.apply(corrected)
        self.assertEqual(self.store.records()[0]["text"], corrected["text"])
        self.assertEqual(self.store.pending(), [])

    def test_user_fact_requires_user_evidence_and_partial_speech_cannot_be_promoted(self):
        ids = self.exchange("一个问题。", "她猜用户住在上海。")
        with self.assertRaises(ValueError):
            self.apply(record("card.user", "用户住上海", [ids[1]], kind="user"))
        partial = self.store.append("old-session", "assistant", "我从小住在", delivery="interrupted")
        with self.assertRaises(ValueError):
            self.apply(record("card.character", "新童年", [partial], kind="character"))

    def test_hidden_records_do_not_enter_actor_or_writer_context(self):
        ids = self.exchange("用于知识可见性检查的合成证据。")
        self.apply(record("episode.hidden", "不可知暗号 BRONZE", ids, known=False, keywords=["BRONZE"]))
        self.assertEqual(len(self.store.records(include_hidden=True)), 1)
        sent = Conversation(self.pack, self.store).begin("BRONZE 是什么？")
        self.assertNotIn("不可知暗号", str(sent))
        self.assertEqual(self.store.batch().records, ())

    def test_identity_seed_and_concurrent_process_guard(self):
        with self.assertRaisesRegex(ValueError, "already open"):
            self.open()
        self.store.close()
        try:
            for kwargs in ({"user": "someone-else"}, {"character": "other"}, {"seed": "different"}):
                with self.assertRaisesRegex(ValueError, "differs"):
                    self.open(**kwargs)
        finally:
            self.store = self.open()

    def test_backlog_overflow_does_not_silently_drop_unprocessed_corrections(self):
        for i in range(8):
            self.store.append("old", "user", f"纠正{i}:" + "内容" * 1500)
        with self.assertRaises(BacklogFull):
            self.store.context("new", "你好")
        self.assertEqual(len(self.store.pending()), 8)
        self.assertEqual(self.store.watermark, 0)
        while self.store.batch():
            self.apply()
        context, _ = self.store.context("new", "你好")
        self.assertEqual(context["unprocessed"], [])

    def test_forget_scrubs_related_cards_history_and_evidence_but_not_unrelated(self):
        # Independent information predates the secret and never consumed it.
        other = self.exchange("无关展览信息。")
        self.apply(record("episode.other", "无关展览信息", other, keywords=["展览"]))
        ids = self.exchange("唯一需要忘记的暗号是 REDMOON。")
        self.apply(record("card.user", "暗号 REDMOON", ids, kind="user"),
                   record("episode.code", "暗号 REDMOON", ids, keywords=["暗号"]))
        count = self.store.forget("episode.code")
        self.assertEqual(count, 2)
        self.assertEqual([r["id"] for r in self.store.records()], ["episode.other"])
        all_text = str([r[0] for r in self.store.db.execute("SELECT text FROM events")]) + str(self.store.records())
        self.assertNotIn("REDMOON", all_text)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 0)
        sent = Conversation(self.pack, self.store).begin("暗号是什么？")
        self.assertNotIn("REDMOON", str(sent))

    def test_forget_also_removes_an_uncited_reply_that_repeats_the_fact(self):
        ids = self.exchange("我的暗号是 MOONSTONE。", "好，我听到了 MOONSTONE。")
        self.apply(record("card.user", "暗号 MOONSTONE", [ids[0]], kind="user"))
        self.store.forget("card.user")
        context, history = self.store.context("old-session", "暗号是什么？")
        self.assertNotIn("MOONSTONE", str((context, history)))

    def test_forget_follows_raw_history_consumption_without_a_selected_record(self):
        chat = Conversation(self.pack, self.store)
        chat.begin("暗号是 SILVERDRUM。")
        chat.delivered("知道了。")
        self.apply(record("card.user", "暗号 SILVERDRUM", [1], kind="user"))
        self.exchange("暂时撤下这张卡。", session="maintenance-only")
        self.apply(retire=["card.user"])
        sent = chat.begin("刚刚的暗号呢？")
        self.assertIn("SILVERDRUM", str(sent))
        self.assertEqual(self.store.select("暗号"), [])
        chat.delivered("刚说的是 SILVERDRUM。")
        self.store.forget("card.user")
        self.assertNotIn("SILVERDRUM", str(chat.begin("继续聊")))
        self.assertNotIn("SILVERDRUM", str(self.store.pending()))

    def test_forget_follows_maintenance_inputs_even_if_model_omits_old_citations(self):
        ids = self.exchange("暗号是 REDMOON。")
        self.apply(record("card.user", "暗号 REDMOON", ids, kind="user"))
        new = self.exchange("再存一条。", session="independent")
        # A writer can copy from a selected card while citing only the new exchange.
        self.apply(record("episode.copied", "暗号 REDMOON", new))
        self.store.forget("card.user")
        self.assertEqual(self.store.records(), [])
        self.assertNotIn("REDMOON", str(Conversation(self.pack, self.store).begin("暗号")))

    def test_forget_replacement_removes_retired_version_and_its_original_evidence(self):
        ids = self.exchange("展览先约周六。")
        self.apply(record("episode.saturday", "展览约周六", ids, keywords=["展览"]))
        corrected = self.exchange("展览改周日。")
        self.apply(record("episode.sunday", "展览改周日", corrected, keywords=["展览"]), retire=["episode.saturday"])
        self.store.forget("episode.sunday")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM records").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM terms").fetchone()[0], 0)
        self.assertTrue(all(not r[0] for r in self.store.db.execute("SELECT text FROM events")))

    def test_backup_restores_state_and_cannot_overwrite_existing_file(self):
        ids = self.exchange("用于备份的合成信息。")
        self.apply(record("card.user", "合成信息", ids, kind="user"))
        backup = self.root / "backup.sqlite3"
        self.store.backup(backup)
        with self.assertRaises(FileExistsError):
            self.store.backup(backup)
        restored = MemoryStore(backup, character=self.pack.id, user="test-user", seed=seed_digest(self.pack))
        try:
            self.assertEqual(restored.records(), self.store.records())
        finally:
            restored.close()


class FakeProvider:
    def __init__(self, build, *, started=None, release=None):
        self.build, self.started, self.release = build, started, release
        self.requests = []

    async def chat(self, messages, **kwargs):
        self.requests.append(messages)
        if self.started:
            self.started.set()
        if self.release:
            await self.release.wait()
        value = self.build(json.loads(messages[-1]["content"]))
        yield value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class MaintenanceTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    async def test_writer_normalizes_only_redundant_existing_card_retirement(self):
        old = self.exchange("周六下午有空。")
        self.apply(record("card.user", "周六下午有空", old, kind="user"))
        new = self.exchange("周日下午有空，看展仅为设想。")
        raw = {"updates": [record("card.user", "周日下午有空，看展仅为设想", new, kind="user")],
               "retire": ["card.user"]}
        provider = FakeProvider(lambda p: raw)
        worker = Maintainer(self.store, self.pack, provider)
        self.assertTrue(await worker.once())
        self.assertEqual(worker.last_attempt["original_proposal"], raw)
        self.assertEqual(worker.last_attempt["normalized_card_replacements"], ["card.user"])
        self.assertEqual(worker.last_attempt["status"], "committed")
        self.assertEqual(worker.last_attempt["through"], new[-1])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(self.store.records()[0]["text"], raw["updates"][0]["text"])
        await worker.cancel()
        self.assertIsNone(worker.last_attempt)

    async def test_normalization_does_not_hide_invalid_proposals_or_bad_evidence(self):
        old = self.exchange("topic information")
        self.apply(record("card.user", "old", old, kind="user"),
                   record("episode.topic", "topic", old),
                   record("card.character", "hidden", old, kind="character", known=False))
        new = self.exchange("new topic")
        update = record("card.user", "new", new, kind="user")
        proposals = [
            {"updates": [update], "retire": ["card.user", "card.user"]},
            {"updates": [update, update], "retire": ["card.user"]},
            {"updates": [record("episode.topic", "new", new)], "retire": ["episode.topic"]},
            {"updates": [record("card.active", "new", new, kind="active")], "retire": ["card.active"]},
            {"updates": [record("card.character", "new", new, kind="character")], "retire": ["card.character"]},
            {"updates": [{**update, "kind": "relationship"}], "retire": ["card.user"]},
            {"updates": [{**update, "evidence": [999]}], "retire": ["card.user"]},
        ]
        before, watermark = self.store.records(include_hidden=True), self.store.watermark
        for proposal in proposals:
            worker = Maintainer(self.store, self.pack, FakeProvider(lambda p: proposal))
            self.assertFalse(await worker.once())
            self.assertEqual(worker.last_attempt["status"], "rejected")
            self.assertEqual(self.store.records(include_hidden=True), before)
            self.assertEqual(self.store.watermark, watermark)
            self.assertEqual(len(self.store.pending()), 2)

    async def test_forget_removes_cross_session_repetitions_before_next_maintenance(self):
        chat = Conversation(self.pack, self.store)
        chat.begin("我的暗号是 REDMOON。")
        chat.delivered("听到了。")
        self.apply(record("card.user", "暗号 REDMOON", [1], kind="user"))
        self.store.close()
        self.store = self.open()
        chat = Conversation(self.pack, self.store)
        sent = chat.begin("暗号是什么？")
        self.assertIn("REDMOON", str(sent))
        chat.delivered("是 REDMOON。")
        self.apply(record("episode.echo", "她复述了 REDMOON", [4], keywords=["暗号"]))
        chat = Conversation(self.pack, self.store)
        chat.begin("暗号再说一遍？")
        chat.delivered("REDMOON。")
        self.store.forget("card.user")
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.pending(), [])
        for row in self.store.db.execute("SELECT text FROM events"):
            self.assertNotIn("REDMOON", row[0])
        sent = chat.begin("聊聊装裱吧")
        self.assertNotIn("REDMOON", str(sent))
        chat.delivered("可以先聊画框。")
        provider = FakeProvider(lambda p: {"updates": [], "retire": []})
        self.assertTrue(await Maintainer(self.store, self.pack, provider).once())
        self.assertNotIn("REDMOON", str(provider.requests))

    async def test_background_commit_does_not_block_frontstage_and_next_session_consumes_it(self):
        self.exchange("我在准备修复师考试。")
        started, release = asyncio.Event(), asyncio.Event()
        provider = FakeProvider(lambda p: {"updates": [record("card.user", "用户在准备修复师考试。",
                                                             [p["batch"]["events"][0]["seq"]], kind="user")], "retire": []},
                                started=started, release=release)
        worker = Maintainer(self.store, self.pack, provider)
        worker.schedule()
        await started.wait()
        sent = Conversation(self.pack, self.store).begin("考试的事我刚提过。")
        self.assertIn("修复师考试", sent[0]["content"])
        self.assertFalse(worker.task.done())
        release.set()
        await worker.task
        self.assertIsNone(worker.error)
        self.assertIn("修复师考试", Conversation(self.pack, self.store).begin("还记得我最近在忙什么吗？")[0]["content"])

    async def test_invalid_model_output_leaves_watermark_and_evidence_unchanged(self):
        self.exchange("不可丢失的纠正。")
        provider = FakeProvider(lambda p: "not JSON")
        worker = Maintainer(self.store, self.pack, provider)
        self.assertFalse(await worker.once())
        self.assertEqual(worker.error, "JSONDecodeError")
        self.assertEqual(self.store.watermark, 0)
        self.assertEqual(len(provider.requests), 1)
        self.assertIn("不可丢失的纠正", Conversation(self.pack, self.store).begin("继续")[0]["content"])

    async def test_delete_during_maintenance_rejects_late_write(self):
        self.exchange("不应回来 SECRET_WORD。")
        started, release = asyncio.Event(), asyncio.Event()
        provider = FakeProvider(lambda p: {"updates": [record("card.user", "SECRET_WORD",
                                                             [p["batch"]["events"][0]["seq"]], kind="user")], "retire": []},
                                started=started, release=release)
        worker = Maintainer(self.store, self.pack, provider)
        worker.schedule()
        await started.wait()
        self.store.forget()
        release.set()
        await worker.task
        self.assertEqual(worker.error, "StaleBatch")
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.pending(), [])

    async def test_cancelled_task_can_be_followed_by_a_fresh_batch(self):
        self.exchange("一条新消息。")
        started, release = asyncio.Event(), asyncio.Event()
        provider = FakeProvider(lambda p: {"updates": [], "retire": []}, started=started, release=release)
        worker = Maintainer(self.store, self.pack, provider)
        worker.schedule()
        await started.wait()
        await worker.cancel()
        release.set()
        await worker.flush()
        self.assertEqual(self.store.pending(), [])


if __name__ == "__main__":
    unittest.main()
