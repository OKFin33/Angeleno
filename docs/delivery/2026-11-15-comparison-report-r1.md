# 文字同料比较报告 r1

Owner Zab 在 2026-11-15 05:04（Asia/Taipei）批准这项交付，原话是「前面的任务，可以做」。
本文记录交付点 A 要交什么、现在交到哪、哪些格子必须留空。它不是已打分的比较结果。

比较要回答的是：Angeleno 是否更像向一个来自洛杉矶的朋友打听当地，而不是在问一个同料的普通助手（[docs/intent.md:11](../intent.md)，[docs/spec.md:211](../spec.md)）。
文字入口用来避开识别噪声；这一项不用 GPU（[docs/spec.md:211](../spec.md)）。
同料是同一核心、同一模型、同一地方资料和工具上限，只换人物材料，不是和 ChatGPT 比（[docs/spec.md:211](../spec.md)）。

## 现在的结论

正式比较没有跑。因此没有偏好计数，也没有「多数更像朋友」或「关键事实不比助手差」的观察结果。

[docs/spec.md:258](../spec.md) 的产品条件是这条结论本身：保留情景里多数判 Angeleno 更像朋友，理由能指回对话，并且关键事实不比同料助手差。
报告表格填满不等于这个条件成立。条件成立，或者如实写成不成立，两种都算这份报告交付。现在两种都还没有产生，不能把本文的完整程度当成通过。

`scripts/check_comparison_report.py` 在本检出上退出码不是 0。它会写明缺哪些输入。冻结指纹那一项仍然通过。

## 要交的报告

在冻结候选上，Angeleno 和同料普通助手各走一遍未见保留情景的文字入口，用 Zab 校准过的口径打分，并给出 E3–E8 与五条工程边界的通过／失败表。
报告要能按同一命令复核。

冻结候选仍是 `4185b8eb29e8fa62ddc32300cc9db526f6704e15`，见 [examples/comparison/freeze-checklist.json](../../examples/comparison/freeze-checklist.json)。
最新交付合入是 [PR #35](https://github.com/OKFin33/Angeleno/pull/35) 的合并提交 `6103c463d8530a0dced139161906cf13b5767af6`（其前已合入 PR #34）。main 上多出来的是比较控制记录和说明，不自动把冻结候选改掉。人物会话循环若再改，清单要重建。

复核命令：

```sh
.venv/bin/python scripts/check_comparison_report.py
```

退出码 0 才算六项都过。现在预期不是 0。六项是：

1. `scripts/check_comparison_freeze.py` 仍然退出码 0，冻结指纹不变。
2. 报告里的 `owner_calibration` 非空：D1、D2、D3 各有判断、理由和评分者名字（[docs/spec.md:253](../spec.md)）。
3. 未见保留集 4–6 段（[docs/spec.md:245](../spec.md)）。作者声明没有读过 D1–D3。两臂跑在同一个候选提交上。没有未处理的 `branch_uncovered`。正文不进调优树（[docs/spec.md:247](../spec.md)）。
4. E3–E8 和五条工程边界各有 pass 或 fail。证据不足不算通过（[docs/spec.md:257](../spec.md)）。E7 没有实机记录时不能写成 pass。
5. 打印偏好计数和结论，并写明评分者能认出两臂，这不是盲评（[docs/spec.md:253](../spec.md)）。结论按 [docs/spec.md:258](../spec.md) 写成立或不成立。
6. `make verify` 在输入齐了之后仍然通过；仓库跟踪路径里没有 private、账本或密钥。输入不齐时，不拿 verify 的通过代替比较。

机器可读报告的路径约定是 `examples/comparison/comparison-report.json`。这个文件故意不在仓库里。正式运行的回执留在操作者本地，不把密钥、账本或保留集正文提交进来。

## 里程碑

A1 和 A2 可以并行。后面是 A3，然后 A4，然后 A5。

| 里程碑 | 状态 | 做什么 |
| --- | --- | --- |
| A1 边界映射 | 本变更已做 | E3–E8 和五条工程边界各自指向一条现成回归，或者标成「无离线测试」并有测试锁住这个缺口。pytest 仍要全过。映射不是比较分数。 |
| A2 未见保留集 | 堵住 | 4–6 段，由没读过 D1–D3 的新执行者只按 Intent 和 Spec §8 来写。hash 以后写进冻结清单，正文不进调优树（[docs/spec.md:245](../spec.md)，[docs/spec.md:247](../spec.md)）。仓库已公开，Zab 还没有指定保管人，所以这段不能开始。 |
| A3 开发输出与校准 | Zab 保留 | 在冻结候选上重跑 D1–D3 两臂。这是付费 DeepSeek 调用，要密钥和预算。校准只由 Zab 做。 |
| A4 正式运行 | 还没开始 | 同一环境里跑未见保留集。同样要密钥和预算。 |
| A5 打分与报告 | 还没开始 | 用 A3 的口径打分，另用一个不同模型做第二评分者，写出报告，让检查脚本退出码 0。 |

H1–H5 维持原标「起草时已读 D1–D3，仅作可见练习题，不作未见保留集」，不改写，也不当成 A2。

## A1 边界映射

规格里的情景判断见 [docs/spec.md:238](../spec.md) 到 [docs/spec.md:243](../spec.md)。五条工程边界见 [docs/spec.md:249](../spec.md)。
正常和失败的工具路径先用离线固定证据盖住；盖不住的交回缺证据，不把离线模拟写成实机证据（[docs/spec.md:203](../spec.md)，[docs/spec.md:247](../spec.md)）。
机器可读的映射在 `src/angeleno/comparison_boundaries.py`，`tests/test_comparison_boundaries.py` 核对每个函数就在所写的那一行。

| 编号 | 规格在看什么 | 离线回归 |
| --- | --- | --- |
| E3 | 普通规律和临时例外冲突；用相关来源和洛杉矶日期，结论与卡片能追溯 | `tests/test_places_check.py:88` `test_dated_exception_beats_standing_faq_and_social`；`tests/test_live_sources.py:310` `test_published_hours_and_recurring_exceptions_follow_asked_calendar` |
| E4 | 未收录、不熟、人流；未收录不是永久盲区，证据不够就承认未知 | `tests/test_places_catalog.py:71` `test_gap_is_not_a_nonexistence_claim`；`tests/test_places_check.py:139` `test_unknown_missing_date_and_out_of_scope`。这两条不证明模型对白没有编造实时人流 |
| E5 | 空白、超时、同页冲突、工具限额；保留不确定，失败后还能说话，不无限重试 | `tests/test_places_check.py:116` `test_equal_rank_conflict_does_not_pick_a_side`；`tests/test_places_check.py:165` `test_disabled_sources_do_not_open_a_body`；`tests/test_lookup_tool_limits.py:107` `test_model_ignoring_disabled_tools_never_gets_fifth_request`；`tests/test_voice_deadlines.py:419` `test_expected_source_unavailable_is_spoken_and_call_continues` |
| E6 | 查询中补充、取消或换题，旧结果迟到；新条件有效，旧结果不播、不盖卡片、不写成已完成交流 | `tests/test_lookup.py:112` `test_query_supplement_cancel_and_topic_change`；`tests/test_places_events.py:71` `test_cancel_drops_a_late_conclusion`；`tests/test_places_events.py:125` `test_new_topic_replaces_the_card_without_adopting_the_old_conclusion`；`tests/test_web_lookup.py:460` `test_late_search_cannot_repopulate_candidates_after_cancel` |
| E7 | 播放中插话、挂断后重拨、没播完的结果 | **无离线测试**。实机播放不能用离线事件模拟代替（[docs/spec.md:203](../spec.md)，[docs/trial.md:105](../trial.md)）。合成会话里已有 `tests/test_voice_lifecycle.py:110`、`:127`、`:141`，只说明记忆和交付标记怎么走，不算 E7 通过 |
| E8 | 人物虚构、过去查证和用户纠正混在一起；三类事实不混写，纠正生效，没有假的共同经历 | `tests/test_memory_preference_reopen.py:23` `test_temporary_fact_role_preference_and_user_correction_persist_in_right_domains`；`tests/test_attribution_candidate.py:95` `test_new_explicit_correction_replaces_old_preference_without_touching_author_past`；`tests/test_context_quality.py:162` `test_explicit_date_and_character_fiction_remain_usable`。这三条不代替模型有没有编出共同经历 |
| 不保存 | 用户要求不保存时，新增记录不落盘 | `tests/test_cli.py:203` `test_private_mode_search_never_saves_or_maintains`；`tests/test_shared_tools.py:79` `test_temporary_combined_turn_has_no_new_persistence`；`tests/test_source_card.py:135` `test_partial_delivery_and_temporary_chat_do_not_record_the_card`；`tests/test_voice_deadlines.py:435` `test_temporary_timeout_adds_no_persistent_events_or_results` |
| 跨身份 | 另一个身份不能读记忆或结果，也不能收到卡片 | `tests/test_memory.py:181` `test_identity_seed_and_concurrent_process_guard`；`tests/test_memory_isolation.py:53` `test_development_or_unlabelled_open_cannot_write_real_store`；`tests/test_places_events.py:144` `test_sessions_do_not_share_a_card`；`tests/test_voice_cards.py:158` `test_missing_dispatch_recipient_refuses_card_broadcast_and_closes_store` |
| 恶意来源 | 页面指令不能扩权；不安全 URL、非公网地址和 API 跳转被拒绝；直连私网 DNS 被拒绝；供应商 Fetch 看不到的解析按实标注，403 停本批 | `tests/test_web_evidence_reading.py:122` `test_web_instructions_remain_tool_data_and_raw_json_cannot_label_itself`；`tests/test_web_lookup.py:540` `test_local_dns_categories_remain_diagnostic_only`；`tests/test_web_lookup.py:556` `test_remote_fetch_connects_only_to_api_without_candidate_dns`；`tests/test_web_lookup.py:578` `test_lexically_unsafe_remote_urls_never_reserve_or_send`；`tests/test_web_lookup.py:597` `test_provider_403_stops_batch_even_after_adapter_reconstruction`；`tests/test_web_lookup.py:618` `test_fetch_api_redirect_is_not_followed`；`tests/test_live_sources.py:66` `test_dns_result_is_the_vetted_connection_address_list` |
| 额度 | 请求、费用或识别额度用尽就停相应能力，不换一本账绕开 | `tests/test_budget.py:96` `test_unknown_unavailable_currency_and_low_balance_never_post`；`tests/test_budget.py:192` `test_foreign_ledger_is_untouched`；`tests/test_audio_adapters.py:94` `test_asr_allowance_stops_audio_before_unreserved_second`；`tests/test_cli.py:263` `test_tool_followup_checks_new_balance_and_stops_without_second_charge` |
| 状态不进对白 | 状态和来源事件不进 TTS，也不当成已经说出的话 | `tests/test_places_events.py:38` `test_result_stays_off_the_speech_channel`；`tests/test_voice_deadlines.py:446` `test_status_is_addressed_ordered_text_free_and_closed_on_hangup`；`tests/test_source_card.py:49` `test_card_is_stored_only_after_delivery` |

E7 那一行的「无离线测试」由 `tests/test_comparison_boundaries.py` 锁住：标成 pass 而没有 `kind=device` 的实机记录时，检查脚本拒绝。`insufficient` 同样拒绝。

## 留给 Zab 的空白

下面五项没有观察，也不代填。检查脚本在正式报告里见到空值或「待定」会失败。

| 事项 | 现在 |
| --- | --- |
| D1／D2／D3 校准：判断、理由、评分者 | （空白，不代填） |
| 「关键事实不比助手差」除了 [docs/spec.md:258](../spec.md) 以外还有没有更严的下限 | （空白，不代填） |
| 付费运行的预算和密钥放在哪 | （空白，不代填） |
| 未见保留集的保管人 | （空白，不代填） |
| 比较语言。冻结锚例是中文，产品默认现在是英文 | （空白，不代填） |

第二评分者用哪个不同的模型，也还没定。六项检查不拿这个空位编一个评分者；打印时会把它显示成空白。A5 真正打分时要有这个人，否则不能把单方分数说成已经对过偏差。

## 不在这项里

- 英文 TTS／RTC、打断和重连、人工听感。
- 设备上的 E7。Owner 声音试跑要 GPU，见 [docs/trial.md:103](../trial.md) 到 [docs/trial.md:105](../trial.md)。
- 和 ChatGPT 或其他产品比（[docs/spec.md:211](../spec.md)）。
- 改写 H1–H5。
- 第三轮优化（[docs/spec.md:261](../spec.md)）。

备选 B 是英文语音的 Owner 试跑，等有 GPU 再做。

## 交付之后仍由 Zab 决定

- Jo 作者素材再许可。代码和文档的 MIT、改编文件的 Apache-2.0 不自动覆盖人物素材。
- Release 和 tag。2026-11-15 用 GitHub API `GET /repos/OKFin33/Angeleno` 核对过：`private` 为 false，`visibility` 为 public。这次没有改可见性，也没有打 tag。

## Issue #1 正文替换稿

这里的 GitHub 读取接口不能改 issue。若 #1 还是旧正文，把 `<details>` 之前整段换成下面的文字，`<details>` 历史记录原样保留。

# Angeleno v0.1 收口

Status: active — 仓库已公开，PR #31–#35 已合入，离线收口已完成。正式同料比较还没跑。整体接受、Jo 再许可和 Release／tag 仍未完成。

本项是唯一 v0.1 交付与接纳入口；不是以 PR 数量、CI 或一次通话代替产品接受。范围仍以 [Intent](https://github.com/OKFin33/Angeleno/blob/main/docs/intent.md) 与 [Spec](https://github.com/OKFin33/Angeleno/blob/main/docs/spec.md) 为准。

## 当前交付

- 2026-11-15 用 GitHub API `GET /repos/OKFin33/Angeleno` 核对：`private` 为 false，`visibility` 为 public。这次核对没有改可见性。未创建 Release／tag。
- 最新交付合入是 [PR #35](https://github.com/OKFin33/Angeleno/pull/35)（比较报告 r1 交付说明）的合并提交 **`6103c463d8530a0dced139161906cf13b5767af6`**；[PR #34](https://github.com/OKFin33/Angeleno/pull/34)（离线收口）在其之前合入。[PR #31](https://github.com/OKFin33/Angeleno/pull/31)（公开交接）、[PR #32](https://github.com/OKFin33/Angeleno/pull/32)（v0.1.1 默认英文）、[PR #33](https://github.com/OKFin33/Angeleno/pull/33)（可安装的 `src/angeleno/`）都已在这条 main 上。[PR #30](https://github.com/OKFin33/Angeleno/pull/30) 仍是中文 v0.1.0。同料比较的冻结候选仍是 `4185b8eb29e8fa62ddc32300cc9db526f6704e15`，不是把后来的说明提交自动算进候选。
- 离线收口已完成：干净环境的非 GPU 复现和同料比较控制记在 [trial](https://github.com/OKFin33/Angeleno/blob/main/docs/trial.md) 与 [comparison](https://github.com/OKFin33/Angeleno/blob/main/docs/comparison.md)。这不是正式比较，也不是产品接受。
- 下一交付点是文字同料比较报告 r1：[docs/delivery/2026-11-15-comparison-report-r1.md](https://github.com/OKFin33/Angeleno/blob/main/docs/delivery/2026-11-15-comparison-report-r1.md)。#12 继续持有这项义务。
- 中文 v0.1.0 的随仓 Jo 作者包、README、运行说明和恢复入口仍在。v0.1.1 默认英文已经随 PR #32 合入，不是待做计划。使用者自备有权使用的参考音频、模型权重与服务账户；不分发真实聊天／记忆、参考声音、密钥或私有账本。
- #11 已按真实语音接合阶段交付关闭；原先未通过的完整体验／边界要求转入本项，**未宣称旧全验通过**。#5／#7 未采纳，以 not planned 关闭。#12 只保留尚未完成的正式同料比较，没有取消该义务。
- 服务当前关闭、按需启动。现有部署保留正确 Jo 绑定、原真实身份及隔离后的记忆；开发材料不得落入真实用户身份。ASR 测试预算是本地保护，不是供应商余额或本地 TTS 额度。旧 GPU 暂停／未获 main merge 已不构成当前阻塞。

## 已有真实证据及边界

w16 在 `c40717947ad7b88da9e26c289cef86ed82113ddb` 上有 **8 用户轮／8 回复、8 次程序正常播放完成、0 次中断**。Owner 认可语气、自然表达和所选音色，卡顿暂可接受；不据此宣称所有设备听感、事实与记忆、15／30 秒保护或 E1—E8 整体验收。该场未搜索；没有逐轮完整 HTTP／工具 proposal 原始轨迹，不能编造查询或模型内部原因。真实文字与音频不附本项。

其他接合证据覆盖中文实际 ASR／模型／TTS／native 媒体、取消及重拨、浏览器私有路由合成 PCM、真实公开 Search／卡片与 Fetch 失败降级。受控工具延迟实验、纯合成媒体和部分未完整播放场景各自保留限制；不合并成一个不存在的全链通过结论。后续修复包括真实／开发存储隔离、跨会话含糊输入不自动续旧任务，以及人物表达与记忆归属保护；仍需真实体验复验。

## 剩余收口

- 正式同料比较沿 [比较报告 r1](https://github.com/OKFin33/Angeleno/blob/main/docs/delivery/2026-11-15-comparison-report-r1.md)。D1／D2／D3 的校准、未见保留集保管人、付费预算和密钥位置、比较语言，以及「关键事实不比助手差」有没有额外下限，都还是空白，不代填。
- 在真实设备复验连续播放、打断／重开、首音与长回复体验；确认现实查证、语音和来源卡片一致，失败时诚实但自然，不以强制搜索率验收。英文 TTS／RTC 和设备上的 E7 仍等 GPU。
- 记忆来源与偏好压缩仍有语义局限；可选归因检查并非所有记忆正确的保证。USER.md／显式熟悉状态只是提案，未实现。
- **Jo 作者素材复用许可待 Owner 选择**；代码／文档 MIT、改编文件 Apache-2.0 和第三方声明以 LICENSE／NOTICE 为准。允许入仓不自动确定人物素材再许可。参考声音不随仓。
- 仓库已经公开。Release／tag 和正式发布尚未决定，公开这一步不自动授权打 tag。

下一步按比较报告 r1：A1 边界映射随该文档提交；A2 等 Zab 指定保管人；A3／A4 等密钥和预算。原始历史保留如下，不作为新的暂停或授权门槛。

Issue #12 的当前状态段末建议加上这一句，历史折叠块不动：

下一交付点是 [文字同料比较报告 r1](https://github.com/OKFin33/Angeleno/blob/main/docs/delivery/2026-11-15-comparison-report-r1.md)。Zab 在 2026-11-15 05:04（台北）批准做这一项。正式比较仍未执行。
