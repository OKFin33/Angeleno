"""Synthetic source-quality counterexamples; no model or historical private text."""

import json
from her_agent.author import reading_sections
from dataclasses import replace
import unittest

from her_agent.continuity import Conversation, Maintainer
from test_memory import StoreFixture, FakeProvider, record


class SourceQualityTests(StoreFixture, unittest.TestCase):
    def test_unrelated_and_no_change_batches_preserve_exact_record_and_sources(self):
        old = self.exchange("我喜欢安静的展览。")
        self.apply(record("card.user", "用户喜欢安静的展览。", [old[0]], kind="user"))
        before = self.store.records()
        for topic in ("聊聊木星。", "今天没有新计划。", "那就这样。"):
            self.exchange(topic)
            # Truly identical six fields can retain old sources without adding
            # an unrelated batch as direct evidence.
            self.apply(record("card.user", before[0]["text"], [old[0]], kind="user"))
            self.assertEqual(self.store.records(), before)
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 0)
            self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.revision, 4)

    def test_identical_words_in_different_weeks_update_sources_and_forget_both_versions(self):
        for day in ("2026-10-06", "2026-10-13"):
            ids = self.exchange("下周日有空。")
            created = day + "T09:00:00+00:00"
            with self.store.db:
                self.store.db.execute("UPDATE events SET created=? WHERE seq IN (?,?)", (created, *ids))
            self.apply(record("card.user", "用户下周日有空。", [ids[0]], kind="user"))
        current = self.store.records()[0]
        self.assertEqual(current["evidence"], [3])
        self.assertEqual(current["revision"], 2)
        self.assertEqual(self.store.watermark, 4)
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)
        self.store.close()
        self.store = self.open()
        context, _ = self.store.context("new-week", "")
        self.assertEqual(context["record_sources"][0]["created_range"], [created, created])
        self.assertEqual(self.store.record_evidence(context["records"])[0]["text"], "下周日有空。")
        self.store.forget("card.user")
        self.assertEqual(self.store.context("new-week", "")[0]["record_sources"], [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 0)
        self.assertTrue(all(not e[0] for e in self.store.db.execute("SELECT text FROM events")))

    def test_same_text_with_new_assistant_only_citations_is_an_actual_invalid_user_update(self):
        ids = self.exchange("喜欢安静。")
        value = record("card.user", "喜欢安静。", [ids[0]], kind="user")
        self.apply(value)
        before = self.store.records()
        new = self.exchange("新话题。", "喜欢安静。")
        with self.assertRaisesRegex(ValueError, "assistant speech"):
            self.apply({**value, "evidence": [new[1]]})
        self.assertEqual(self.store.records(), before)
        self.assertEqual(self.store.watermark, 2)
        self.assertEqual(len(self.store.pending()), 2)

    def test_four_full_cards_and_selected_episodes_have_bounded_source_metadata(self):
        citations = {kind: [] for kind in ("active", "user", "relationship", "character")}
        sizes = []
        for count in range(1, 65):
            for kind, evidence in citations.items():
                ids = self.exchange(f"剧场资料{kind}第{count}条。", "剧场往事兼容虚构。")
                evidence.append(ids[1] if kind == "character" else ids[0])
                self.apply(record("card." + kind, f"剧场资料{kind}共{count}项。", evidence[:],
                                  kind=kind, keywords=["剧场"]))
            if count in (36, 64):
                context, history = self.store.context("reopened", "剧场")
                self.assertEqual(len(history), 20)
                self.assertEqual(self.store.pending(), [])
                self.assertEqual(len(context["record_sources"]), 4)
                self.assertEqual(sum(x["count"] for x in context["record_sources"]), 4 * count)
                self.assertEqual(len(self.store.record_evidence(context["records"])), 4 * count)
                sizes.append(len(json.dumps(context["record_sources"], ensure_ascii=False)))
        # Increasing citations from 144 to 256 does not increase metadata rows.
        self.assertEqual(sizes[0], sizes[1])
        for n in range(6):
            evidence = []
            for count in range(1, 65):
                ids = self.exchange(f"剧场经历{n}第{count}条。")
                evidence.append(ids[0])
                self.apply(record(f"episode.theater{n}", f"剧场经历{n}共{count}条。", evidence[:], keywords=["剧场"]))
        before = self.store.records()
        self.store.close()
        self.store = self.open()  # existing schema 2, no migration/reset
        context, _ = self.store.context("reopened", "剧场")
        self.assertEqual(self.store.records(), before)
        self.assertEqual(len(context["records"]), 10)
        self.assertEqual(len(context["record_sources"]), 10)
        self.assertEqual(sum(r["count"] for r in context["record_sources"]), 640)
        self.assertLess(len(json.dumps(context, ensure_ascii=False)), 16000)
        Conversation(self.pack, self.store).begin("剧场聊过什么？")
        self.store.forget("card.character")
        context, _ = self.store.context("after-forget", "剧场")
        self.assertNotIn("card.character", [r["id"] for r in context["record_sources"]])
        actual = {r["id"]: len(set(r["evidence"])) for r in context["records"]}
        self.assertEqual({r["id"]: r["count"] for r in context["record_sources"]}, actual)

    def test_source_range_uses_chronological_instants_not_sequence_or_offset_spelling(self):
        first = self.exchange("较晚发言。")
        second = self.exchange("较早发言。")
        with self.store.db:
            self.store.db.execute("UPDATE events SET created=? WHERE seq=?", ("2026-10-13T10:00:00+00:00", first[0]))
            self.store.db.execute("UPDATE events SET created=? WHERE seq=?", ("2026-10-13T12:00:00+08:00", second[0]))
        self.apply(record("card.user", "用户作过两次发言。", [first[0], second[0]], kind="user"))
        summary = self.store.context("reopened", "")[0]["record_sources"][0]
        self.assertEqual(summary["created_range"], ["2026-10-13T12:00:00+08:00", "2026-10-13T10:00:00+00:00"])
        self.assertEqual(summary["scope"], "all_record_evidence")

    def test_partial_updates_keep_original_basis_across_multiple_maintenance_batches(self):
        preference = self.exchange("我喜欢安静的展览。")
        plan = self.exchange("周六有空。")
        self.apply(record("card.user", "喜欢安静的展览；周六有空。", [preference[0], plan[0]], kind="user"))
        for text, summary in (("改成周日。", "喜欢安静的展览；现改为周日有空。"),
                              ("又改成周一了。", "喜欢安静的展览；现改为周一有空。")):
            new = self.exchange(text)
            # The supplied maintenance decision preserves the preference source,
            # replaces the time source, and does not accumulate superseded times.
            evidence = [preference[0], new[0]]
            self.apply(record("card.user", summary, evidence, kind="user"))
            self.assertEqual(self.store.records()[0]["evidence"], evidence)
        context, _ = self.store.context("reopen", "展览")
        self.assertEqual(context["records"][0]["text"], "喜欢安静的展览；现改为周一有空。")
        self.assertEqual(len(context["record_sources"]), 1)
        summary = context["record_sources"][0]
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["scope"], "all_record_evidence")
        self.assertEqual(summary["roles"], ["user"])
        originals = self.store.record_evidence(context["records"])
        self.assertEqual({e["seq"] for e in originals}, set(evidence))
        self.assertEqual(summary["created_range"], [originals[0]["created"], originals[-1]["created"]])
        before = self.store.records()
        self.store.forget("card.user")
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.record_evidence(before), [])

    def test_assistant_repetition_cannot_supply_new_user_fact(self):
        old = self.exchange("我爱看展。")
        self.apply(record("card.user", "用户爱看展。", [old[0]], kind="user"))
        new = self.exchange("随便聊。", "你住在上海。")
        with self.assertRaisesRegex(ValueError, "source speaker"):
            self.apply(record("card.user", "用户爱看展，住上海。", [old[0], new[1]], kind="user"))
        self.assertEqual(self.store.records()[0]["text"], "用户爱看展。")

    def test_relative_date_and_timestamp_survive_reopen_without_becoming_confirmed_date(self):
        ids = self.exchange("下周日下午有空。", "我猜是2026-10-11。")
        self.apply(record("card.user", "用户说下周日下午有空，绝对日期未确认。", [ids[0]], kind="user"),
                   record("episode.calendar", "角色推算2026-10-11，未获用户确认。", [ids[1]]))
        created = self.store.record_evidence(self.store.records())[0]["created"]
        self.store.close()
        self.store = self.open()
        sent = Conversation(self.pack, self.store).begin("原话是哪天？")
        self.assertIn("用户说下周日下午有空，绝对日期未确认。", sent[0]["content"])
        self.assertIn(created, sent[0]["content"])
        user = next(r for r in self.store.records() if r["kind"] == "user")
        self.assertNotIn("2026-10-11", user["text"])

    def test_explicit_date_and_character_fiction_remain_usable(self):
        ids = self.exchange("2026-10-18下午有空。", "我小时候在后院造过一把歪椅子。")
        self.apply(record("card.user", "用户说2026-10-18下午有空。", [ids[0]], kind="user"),
                   record("card.character", "角色新增虚构：小时候在后院造过歪椅子。", [ids[1]], kind="character"))
        self.assertEqual(len(self.store.records()), 2)

    def test_noop_never_repairs_bad_citations_or_hides_conflicting_retirement(self):
        ids = self.exchange("喜欢安静。")
        self.apply(record("card.user", "喜欢安静。", [ids[0]], kind="user"))
        self.exchange("新话题。")
        before = self.store.records()
        for proposal in ({"updates": [record("card.user", "喜欢安静。", [999], kind="user")], "retire": []},
                         {"updates": [record("card.user", "喜欢安静。", [ids[0]], kind="user")], "retire": ["card.user"]}):
            with self.assertRaises(ValueError):
                self.store.apply(self.store.batch(), proposal)
            self.assertEqual(self.store.records(), before)
            self.assertEqual(len(self.store.pending()), 2)

    def test_empty_batch_proposal_advances_watermark_and_keeps_sources(self):
        ids = self.exchange("周日有空。")
        self.apply(record("card.active", "周日有空。", ids, kind="active"))
        before = self.store.records()
        self.exchange("嗯。")
        self.apply()
        self.assertEqual(self.store.records(), before)
        self.assertEqual(self.store.pending(), [])

    def test_no_change_can_keep_old_citations_without_inventing_current_evidence(self):
        ids = self.exchange("周日有空。")
        value = record("card.user", "周日有空。", [ids[0]], kind="user")
        self.apply(value)
        before = self.store.records()
        self.exchange("聊别的。")
        self.apply(value)
        self.assertEqual(self.store.records(), before)
        self.assertEqual(self.store.pending(), [])

    def test_numeric_ranges_do_not_turn_ordinary_topic_into_a_date(self):
        ids = self.exchange("来聊鼓吧。", "我小时候花了10-15分钟调鼓。")
        self.apply(record("episode.drums", "角色虚构往事：花了10-15分钟调鼓。", [ids[1]]))
        self.assertIn("10-15分钟", self.store.records()[0]["text"])

    def test_long_archived_sources_do_not_block_reopen_after_backlog_is_cleared(self):
        ids, raw = [], []
        for i, size in enumerate((3000, 4000, 5000, 6000)):
            prefix, suffix = f"资料{i}：", f"事项{i}。"
            text = prefix + "字" * (size - len(prefix) - len(suffix)) + suffix
            new = self.exchange(text, "听到了。", session=f"long-{i}")
            ids.append(new[0])
            raw.append(text)
            self.apply(record("card.user", "用户提供事项" + "、".join(str(n) for n in range(i + 1)), ids[:], kind="user"))
        self.assertEqual(self.store.pending(), [])
        self.archive_recent()
        before = self.store.records()
        original = self.store.record_evidence(before)
        self.assertEqual([e["text"] for e in original], raw)
        self.store.close()
        self.store = self.open()
        sent = Conversation(self.pack, self.store).begin("我们聊什么？")
        self.assertEqual(self.store.records(), before)
        self.assertNotIn("字" * 100, str(sent))
        context, _ = self.store.context("new", "资料")
        self.assertEqual(len(context["record_sources"]), 1)
        self.assertEqual(context["record_sources"][0], {
            "id": "card.user", "scope": "all_record_evidence", "count": 4,
            "roles": ["user"], "created_range": [original[0]["created"], original[-1]["created"]]})
        self.assertEqual(len(self.store.pending()), 1)

    def test_35_complete_corrections_do_not_accumulate_current_citations_and_forget_history(self):
        for n in range(36):
            ids = self.exchange(f"更正：计划编号现为{n}，此前全部作废。", f"收到，现在为{n}。", session=f"correction-{n}")
            self.apply(record("card.active", f"计划编号现为{n}，旧编号失效。", ids, kind="active"))
            self.assertEqual(self.store.records()[0]["evidence"], ids)
            self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 35)
        self.store.close()
        self.store = self.open()
        request = Conversation(self.pack, self.store).begin("计划编号？")
        self.assertIn("计划编号现为35", str(request))
        self.assertNotIn("计划编号现为0", str(request))
        self.store.forget("card.active")
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM revisions").fetchone()[0], 0)
        self.assertTrue(all(not e[0] for e in self.store.db.execute("SELECT text FROM events")))

    def test_date_questions_denials_and_other_names_or_spellings_are_not_storage_rejections(self):
        cases = [
            ("十月十八日有空吗？我只是问日期。", "林说可能是2026-10-18。", "用户提问2026-10-18是否有空，未确认。", "user"),
            ("我不是说2026/10/11要去，我不去。", "好，不去。", "用户否认2026-10-11出行。", "active"),
            ("聊聊日历。", "Alex guesses October 11, 2026.", "Alex interpreted 2026-10-11; the user has not confirmed it.", "episode"),
            ("2026年10月18日下午有空。", "收到。", "用户说2026-10-18下午有空。", "user"),
        ]
        for i, (user, assistant, summary, kind) in enumerate(cases):
            ids = self.exchange(user, assistant)
            rid = f"episode.date{i}" if kind == "episode" else "card." + kind
            self.apply(record(rid, summary, ids, kind=kind))
            self.assertEqual(next(r for r in self.store.records() if r["id"] == rid)["text"], summary)

    def test_valid_schema_citations_and_user_role_do_not_prove_date_semantics(self):
        # Explicitly exercise the limit: storage cannot recognize the semantic
        # error in an otherwise valid proposal. A real writer must avoid it.
        ids = self.exchange("2026-10-11？那天我不会去。", "明白，不去。")
        incorrect = record("card.user", "用户确认2026-10-11会去。", [ids[0]], kind="user")
        self.apply(incorrect)
        source = self.store.record_evidence(self.store.records())[0]
        self.assertEqual(source["text"], "2026-10-11？那天我不会去。")
        self.assertEqual(source["role"], "user")
        self.assertEqual(self.store.records()[0]["text"], incorrect["text"])


class MaintenanceSourceTests(StoreFixture, unittest.IsolatedAsyncioTestCase):
    async def test_evidence_first_maintenance_keeps_disclosure_correction_and_relationship_sources(self):
        old = self.exchange("我喜欢安静的展览。", "我是修复师，补这个框只花了十分钟。")
        self.apply(record("card.user", "用户喜欢安静的展览。", [old[0]], kind="user"),
                   record("card.character", "角色已说自己是修复师，补框用了十分钟。", [old[1]], kind="character"),
                   record("episode.frame-time", "角色说补框用了十分钟。", [old[1]], keywords=["补框"]))
        fresh = self.exchange("我又追问你补框用多久了。周日可能去看展，还没决定。",
                              "我刚才说错了，修了两个晚上，不是十分钟。")
        with self.store.db:
            self.store.db.execute("UPDATE events SET created=? WHERE seq<=?", ("2026-10-05T09:00:00+00:00", old[1]))
            self.store.db.execute("UPDATE events SET created=? WHERE seq>?", ("2026-10-06T10:00:00+00:00", old[1]))
        before = self.store.records()
        expected_batch = self.store.batch()
        expected_sources = self.store.record_evidence(expected_batch.records)

        def correct(payload):
            # Check the actual once/provider boundary, not a stand-in renderer.
            self.assertEqual(list(payload), ["batch", "record_evidence", "author_material",
                                             "evidence_domains", "lookup_sources"])
            self.assertEqual(payload["batch"]["events"], list(expected_batch.events))
            self.assertEqual(payload["batch"]["records"], list(expected_batch.records))
            self.assertEqual(payload["record_evidence"], expected_sources)
            self.assertEqual(payload["evidence_domains"], {
                "user_utterances": [old[0], fresh[0]],
                "delivered_agent_utterances": [old[1], fresh[1]],
                "interrupted_agent_utterances": [],
                "author_material": {"origin": "author_not_current_tool_execution", "as_of": self.pack.as_of},
            })
            self.assertEqual(payload["lookup_sources"], {
                "coverage": "delivered_results_for_exposed_evidence_only", "results": [],
                "missing_record": "no_recorded_delivered_result_not_proof_of_no_attempt",
            })
            for doc in self.pack.documents:
                self.assertIn(doc.sha256, payload["author_material"])
                for section in reading_sections(doc.body):
                    self.assertIn(section, payload["author_material"])
                if doc.examples:
                    self.assertIn(doc.examples, payload["author_material"])
            # Handwritten correct decisions only demonstrate that the path still
            # accepts disclosed author facts, valid relationships and corrections.
            return {"updates": [
                record("card.user", "用户喜欢安静的展览；周日可能去看展，未决定。", [old[0], fresh[0]], kind="user"),
                record("card.character", "角色已说自己是修复师；于2026-10-06T10:00:00+00:00改口，互动新增的角色虚构：补框用了两个晚上。", [old[1], fresh[1]], kind="character"),
                record("card.relationship", "用户追问角色补框所需时间，角色当场更正自己的说法。", fresh, kind="relationship"),
                record("episode.frame-correction", "角色于2026-10-06T10:00:00+00:00更正补框时间为两个晚上，而非十分钟。", [fresh[1]], keywords=["补框"]),
            ], "retire": ["episode.frame-time"]}

        provider = FakeProvider(correct)
        writer = Maintainer(self.store, self.pack, provider)
        self.assertTrue(await writer.once())
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(writer.last_attempt["status"], "committed")
        current = {r["id"]: r for r in self.store.records()}
        self.assertEqual(current["card.user"]["evidence"], [old[0], fresh[0]])
        self.assertEqual(current["card.character"]["evidence"], [old[1], fresh[1]])
        self.assertNotIn("episode.frame-time", current)
        self.assertEqual(current["card.relationship"]["evidence"], fresh)
        self.assertNotIn("不介意", current["card.relationship"]["text"])
        self.assertEqual(self.store.pending(), [])
        self.store.close()
        self.store = self.open()
        request = Conversation(self.pack, self.store).begin("补框后来怎么说的？")
        self.assertIn("两个晚上", str(request))
        self.assertIn("用户追问角色", str(request))
        self.assertEqual(self.store.record_evidence(list(current.values()))[0]["created"], "2026-10-05T09:00:00+00:00")
        self.store.forget("card.character")
        self.assertNotIn("card.character", [r["id"] for r in self.store.records()])
        self.assertEqual(self.store.record_evidence(before), [])

    async def test_wrong_subject_and_undisclosed_author_background_remain_semantic_limit(self):
        ids = self.exchange("你刚才说得很快，我又追着问你了。", "是，我讲得太轻巧，实际修了两个晚上。")
        pack = replace(self.pack, documents=(replace(self.pack.documents[0],
                       body=self.pack.documents[0].body + "\n作者补充：林姓周，42岁，住杉木街。"),))
        wrong = [record("card.relationship", "用户不介意被追问。", ids, kind="relationship"),
                 record("card.character", "角色姓周，42岁，住杉木街。", [ids[1]], kind="character")]
        provider = FakeProvider(lambda p: {"updates": wrong, "retire": []})
        writer = Maintainer(self.store, pack, provider)
        self.assertTrue(await writer.once())
        self.assertEqual(writer.last_attempt["status"], "committed")
        self.assertEqual([r["text"] for r in self.store.records()], [r["text"] for r in wrong])
        raw = self.store.record_evidence(self.store.records())
        self.assertEqual([e["seq"] for e in raw], ids)
        self.assertNotIn("杉木街", str(raw))
        self.assertIn("我又追着问你", raw[0]["text"])
        # No speaker/keyword gate, biography deletion, or automatic post-repair.

    async def test_oversized_author_material_fails_before_provider_and_keeps_backlog(self):
        ids = self.exchange("必须保留的积压纠正。")
        before, watermark = self.store.records(), self.store.watermark
        # Same pack object interface; no schema/seed change to the test DB.
        oversized = replace(self.pack, documents=(replace(self.pack.documents[0], body="字" * 64_001),))
        provider = FakeProvider(lambda p: {"updates": [], "retire": []})
        worker = Maintainer(self.store, oversized, provider)
        self.assertFalse(await worker.once())
        self.assertEqual(worker.error, "ValueError")
        self.assertEqual(provider.requests, [])
        self.assertEqual(self.store.records(), before)
        self.assertEqual(self.store.watermark, watermark)
        self.assertEqual([e["seq"] for e in self.store.pending()], ids)

    async def test_structurally_valid_wrong_present_state_and_attribution_are_not_repaired(self):
        # Synthetic equivalents of the observed weekday, transient-inventory
        # and advice->decision failures, plus the real-operation boundary.
        # This deliberately documents an accepted WRONG proposal, not quality.
        sent = self.pack.messages([{"role": "user", "content": "下次聊。"}], today="2026-10-06")
        self.assertIn("周二", sent[0]["content"])
        chat = Conversation(self.pack, self.store)
        chat.begin("那我去吃饭了，下次聊。")
        spoken = "今天周六，冰箱里剩两个鸡蛋。吃完早点回去；我已经替你预订了餐位。"
        chat.delivered(spoken)
        incorrect = [record("card.character", "林通常总有鸡蛋，今天周六。", [2], kind="character"),
                     record("card.user", "用户已经吃完饭并决定回家。", [1], kind="user"),
                     record("episode.booking", "系统已替用户预订餐位。", [2], keywords=["餐位"])]
        provider = FakeProvider(lambda p: {"updates": incorrect, "retire": []})
        worker = Maintainer(self.store, self.pack, provider)
        self.assertTrue(await worker.once())
        self.assertEqual(worker.last_attempt["status"], "committed")
        self.assertEqual([r["text"] for r in self.store.records()], [r["text"] for r in incorrect])
        evidence = self.store.record_evidence(self.store.records())
        self.assertEqual([e["text"] for e in evidence], ["那我去吃饭了，下次聊。", spoken])
        # No keyword filtering, weekday replacement or invented user correction.
        self.assertEqual(self.store.db.execute("SELECT text FROM events WHERE seq=2").fetchone()[0], spoken)

    async def test_topic_closure_alone_cannot_be_detected_as_an_invalid_retirement(self):
        ids = self.exchange("我想煮点青菜。", "我小时候煮面总把锅盖弄响。")
        self.apply(record("episode.pot", "角色虚构往事：小时候煮面把锅盖弄响。", [ids[1]], keywords=["煮面"]))
        self.exchange("煮面这事先聊到这儿，下次聊。", "好，再见。")
        # An otherwise valid retirement can still erase a valid past episode
        # from recall. The maintainer must reason about validity, not just end.
        worker = Maintainer(self.store, self.pack, FakeProvider(lambda p: {"updates": [], "retire": ["episode.pot"]}))
        self.assertTrue(await worker.once())
        self.assertEqual(self.store.records(), [])
        self.assertIn("锅盖", str([r[0] for r in self.store.db.execute("SELECT text FROM events")]))

    async def test_writer_gets_original_evidence_separate_from_consumption_dependencies(self):
        ids = self.exchange("下周日有空。")
        self.apply(record("card.user", "用户说下周日有空。", [ids[0]], kind="user"))
        self.exchange("聊别的吧。")
        provider = FakeProvider(lambda p: {"updates": [], "retire": []})
        self.assertTrue(await Maintainer(self.store, self.pack, provider).once())
        payload = json.loads(provider.requests[0][-1]["content"])
        self.assertEqual([e["seq"] for e in payload["record_evidence"]], [ids[0]])
        self.assertEqual(payload["record_evidence"][0]["text"], "下周日有空。")
        self.assertNotIn(ids[1], [e["seq"] for e in payload["record_evidence"]])
        self.assertIn(ids[1], payload["batch"]["records"][0]["inputs"]["events"])

    async def test_long_originals_are_supplied_complete_to_writer_not_actor(self):
        ids, raw = [], []
        for i, size in enumerate((3000, 4000, 5000, 6000)):
            prefix, suffix = f"原始来源{i}:", "保留事项。"
            text = prefix + "字" * (size - len(prefix) - len(suffix)) + suffix
            new = self.exchange(text, "听到了。", session=f"source-{i}")
            ids.append(new[0])
            raw.append(text)
            self.apply(record("card.user", f"用户提供{i + 1}件事项。", ids[:], kind="user"))
        self.exchange("修正：其中第四项已撤回，其他仍保留。")
        provider = FakeProvider(lambda p: {"updates": [], "retire": []})
        self.assertTrue(await Maintainer(self.store, self.pack, provider).once())
        payload = json.loads(provider.requests[0][-1]["content"])
        self.assertEqual([e["text"] for e in payload["record_evidence"]], raw)
        self.assertIn("第四项已撤回", str(payload["batch"]["events"]))
        self.archive_recent()
        sent = Conversation(self.pack, self.store).begin("接着聊。")
        self.assertNotIn("字" * 100, str(sent))

    async def test_source_budget_failure_preserves_correction_and_does_not_send_partial_writer_input(self):
        evidence = []
        for n in range(11):
            prefix = f"资料{n}："
            ids = self.exchange(prefix + "字" * (6000 - len(prefix)), session=f"long-{n}")
            evidence.append(ids[0])
            self.apply(record("card.user", f"用户有{n + 1}件事项。", evidence[:], kind="user"))
        self.archive_recent()
        before = self.store.records()
        correction = self.exchange("更正：全部撤回。")
        provider = FakeProvider(lambda p: {"updates": [], "retire": []})
        worker = Maintainer(self.store, self.pack, provider)
        self.assertFalse(await worker.once())
        self.assertEqual(worker.error, "ValueError")
        self.assertEqual(provider.requests, [])
        self.assertEqual(self.store.records(), before)
        self.assertEqual([e["seq"] for e in self.store.pending()], correction)
        # Sources remain external to the actor even when full maintenance
        # cannot currently fit. The pending correction still reaches it.
        request = Conversation(self.pack, self.store).begin("保留更正，先聊别的。")
        self.assertIn("更正：全部撤回。", str(request))
        self.assertNotIn("字" * 100, str(request))


if __name__ == "__main__":
    unittest.main()
