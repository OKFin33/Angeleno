"""Map Spec E3–E8 and the five engineering boundaries to offline tests.

A mapped test is a mechanism regression already in the tree. It is not a
same-material score. The status 「无离线测试」 is not a pass.
"""

NO_OFFLINE_TEST = "无离线测试"


def _test(file, line, name):
    return {"file": file, "line": line, "name": name}


BOUNDARIES = (
    {
        "id": "E3",
        "title": "日期例外与可追溯结论",
        "spec": "docs/spec.md:238",
        "status": "mapped",
        "tests": (
            _test("tests/test_places_check.py", 88, "test_dated_exception_beats_standing_faq_and_social"),
            _test("tests/test_live_sources.py", 310, "test_published_hours_and_recurring_exceptions_follow_asked_calendar"),
        ),
    },
    {
        "id": "E4",
        "title": "未收录与证据不足",
        "spec": "docs/spec.md:239",
        "status": "mapped",
        "limit": "这些回归钉住未收录不是“不存在”，证据不足时没有结论。它们不证明模型对白没有编造实时人流。",
        "tests": (
            _test("tests/test_places_catalog.py", 71, "test_gap_is_not_a_nonexistence_claim"),
            _test("tests/test_places_check.py", 139, "test_unknown_missing_date_and_out_of_scope"),
        ),
    },
    {
        "id": "E5",
        "title": "空白、冲突、超时与工具限额",
        "spec": "docs/spec.md:240",
        "status": "mapped",
        "tests": (
            _test("tests/test_places_check.py", 116, "test_equal_rank_conflict_does_not_pick_a_side"),
            _test("tests/test_places_check.py", 165, "test_disabled_sources_do_not_open_a_body"),
            _test("tests/test_lookup_tool_limits.py", 107, "test_model_ignoring_disabled_tools_never_gets_fifth_request"),
            _test("tests/test_voice_deadlines.py", 419, "test_expected_source_unavailable_is_spoken_and_call_continues"),
        ),
    },
    {
        "id": "E6",
        "title": "补充、取消、换题与迟到结果",
        "spec": "docs/spec.md:241",
        "status": "mapped",
        "tests": (
            _test("tests/test_lookup.py", 112, "test_query_supplement_cancel_and_topic_change"),
            _test("tests/test_places_events.py", 71, "test_cancel_drops_a_late_conclusion"),
            _test("tests/test_places_events.py", 125, "test_new_topic_replaces_the_card_without_adopting_the_old_conclusion"),
            _test("tests/test_web_lookup.py", 460, "test_late_search_cannot_repopulate_candidates_after_cancel"),
        ),
    },
    {
        "id": "E7",
        "title": "播放中插话、挂断重拨、未播完结果",
        "spec": "docs/spec.md:242",
        "status": NO_OFFLINE_TEST,
        "reason": "实机播放、插话和重拨不能用离线事件模拟代替（docs/spec.md:203，docs/trial.md:105）。",
        "tests": (),
        "mechanism_not_acceptance": (
            _test("tests/test_voice_lifecycle.py", 110, "test_real_session_full_playout_and_redial_pending_context"),
            _test("tests/test_voice_lifecycle.py", 127, "test_real_session_hangup_without_alignment_never_saves_generated_full_text"),
            _test("tests/test_voice_lifecycle.py", 141, "test_real_session_interrupt_uses_only_synchronized_prefix"),
        ),
    },
    {
        "id": "E8",
        "title": "虚构、现实与用户纠正",
        "spec": "docs/spec.md:243",
        "status": "mapped",
        "limit": "这些回归钉住记忆分域和纠正不改写作者过去。它们不代替模型有没有编出共同经历。",
        "tests": (
            _test("tests/test_memory_preference_reopen.py", 23, "test_temporary_fact_role_preference_and_user_correction_persist_in_right_domains"),
            _test("tests/test_attribution_candidate.py", 95, "test_new_explicit_correction_replaces_old_preference_without_touching_author_past"),
            _test("tests/test_context_quality.py", 162, "test_explicit_date_and_character_fiction_remain_usable"),
        ),
    },
    {
        "id": "no_persist",
        "title": "用户要求不保存时不落盘",
        "spec": "docs/spec.md:249",
        "status": "mapped",
        "tests": (
            _test("tests/test_cli.py", 203, "test_private_mode_search_never_saves_or_maintains"),
            _test("tests/test_shared_tools.py", 79, "test_temporary_combined_turn_has_no_new_persistence"),
            _test("tests/test_source_card.py", 135, "test_partial_delivery_and_temporary_chat_do_not_record_the_card"),
            _test("tests/test_voice_deadlines.py", 435, "test_temporary_timeout_adds_no_persistent_events_or_results"),
        ),
    },
    {
        "id": "cross_identity",
        "title": "跨身份不能读记忆或收卡片",
        "spec": "docs/spec.md:249",
        "status": "mapped",
        "tests": (
            _test("tests/test_memory.py", 181, "test_identity_seed_and_concurrent_process_guard"),
            _test("tests/test_memory_isolation.py", 53, "test_development_or_unlabelled_open_cannot_write_real_store"),
            _test("tests/test_places_events.py", 144, "test_sessions_do_not_share_a_card"),
            _test("tests/test_voice_cards.py", 158, "test_missing_dispatch_recipient_refuses_card_broadcast_and_closes_store"),
        ),
    },
    {
        "id": "untrusted_page",
        "title": "恶意页面、不安全地址、私网与 403",
        "spec": "docs/spec.md:249",
        "status": "mapped",
        "tests": (
            _test("tests/test_web_evidence_reading.py", 122, "test_web_instructions_remain_tool_data_and_raw_json_cannot_label_itself"),
            _test("tests/test_web_lookup.py", 540, "test_local_dns_categories_remain_diagnostic_only"),
            _test("tests/test_web_lookup.py", 556, "test_remote_fetch_connects_only_to_api_without_candidate_dns"),
            _test("tests/test_web_lookup.py", 578, "test_lexically_unsafe_remote_urls_never_reserve_or_send"),
            _test("tests/test_web_lookup.py", 597, "test_provider_403_stops_batch_even_after_adapter_reconstruction"),
            _test("tests/test_web_lookup.py", 618, "test_fetch_api_redirect_is_not_followed"),
            _test("tests/test_live_sources.py", 66, "test_dns_result_is_the_vetted_connection_address_list"),
        ),
    },
    {
        "id": "quota_stop",
        "title": "额度用尽即停且不换账本",
        "spec": "docs/spec.md:249",
        "status": "mapped",
        "tests": (
            _test("tests/test_budget.py", 96, "test_unknown_unavailable_currency_and_low_balance_never_post"),
            _test("tests/test_budget.py", 192, "test_foreign_ledger_is_untouched"),
            _test("tests/test_audio_adapters.py", 94, "test_asr_allowance_stops_audio_before_unreserved_second"),
            _test("tests/test_cli.py", 263, "test_tool_followup_checks_new_balance_and_stops_without_second_charge"),
        ),
    },
    {
        "id": "status_not_speech",
        "title": "状态和来源事件不进对白",
        "spec": "docs/spec.md:249",
        "status": "mapped",
        "tests": (
            _test("tests/test_places_events.py", 38, "test_result_stays_off_the_speech_channel"),
            _test("tests/test_voice_deadlines.py", 446, "test_status_is_addressed_ordered_text_free_and_closed_on_hangup"),
            _test("tests/test_source_card.py", 49, "test_card_is_stored_only_after_delivery"),
        ),
    },
)


def boundary_ids():
    return tuple(item["id"] for item in BOUNDARIES)


def by_id(boundary_id):
    for item in BOUNDARIES:
        if item["id"] == boundary_id:
            return item
    raise KeyError(boundary_id)


def e7_verdict_allowed(verdict, device_evidence):
    """E7 pass requires a device record. Offline gaps and thin evidence are not pass."""
    if verdict == "fail":
        return True
    if verdict != "pass" or not isinstance(device_evidence, dict):
        return False
    note = device_evidence.get("note")
    return device_evidence.get("kind") == "device" and isinstance(note, str) and bool(note.strip())
