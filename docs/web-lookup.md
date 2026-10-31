# 主动发现与网页阅读候选

2026-10-31 Owner 明确选择主动发现新资料，替代仅预登记来源。人物性格／成长经历影响搜索和取舍；有效产品约定见 [Intent](intent.md) 与 [Spec](spec.md)，实施与真实验证回 [#11](https://github.com/OKFin33/Angeleno/issues/11)。本候选基于 UI #21 `7cdb9df`，不修改该固定 PR。旧 reader 保留回退和历史证据，不继续扩站点提取器。

## 同一条运行路径

`ConversationRuntime → lookup.reply → WebLookup → DeepSeekWeb → Search / Fetch → ResultChannel → VoiceCards / 页面`。

演员可直接闲聊、查询原有记忆或稳定地方条目，也可提出至多128字符的公开查询词，选择当前轮搜索返回的一个 `result_id` 阅读。没有强制每轮联网或预先问完偏好的脚本。统一总数仍是每轮最多三次工具、每类一次、四次模型请求；Search最多五条，Fetch一页，外部等待合计10秒，无重试。两种网络动作接既有15／30秒保护；这不证明真实语音延迟已通过。

卡片新增 `evidence`（已取得资料）状态，conclusion为空；搜索摘要与正文不是已核实事实。正文超4000字符返回截断／信息不足，保留未知，不从截掉的例外推断开放。HTTP响应限128KiB。发布日期、读取时刻和问题适用日期分开；字段缺失不猜。来源文字只作为tool数据，演员收到明确的非指令约束；模型实际遵循程度仍须真实情景验证。

显式取消结束查询关系；读取被插话中断只取消旧请求，后继 `supplement` 保留原始话语并增加版本。旧请求仍不可发布、播报或写成已完成交流。已读证据只有该轮实际完整交付时才沿原记忆入口留存；临时“不保存”模式仍不落盘。

## 运行开关与计费

既有模型／人物配置和统一账本继续原地使用。默认 `ANGELENO_WEB_ENABLED=0`，无新网络动作。实际端点获准后，操作者才能在该进程显式设 `ANGELENO_WEB_ENABLED=1` 和 `ANGELENO_WEB_REQUEST_LIMIT`（首批1–6）；CLI仍使用已有 `ANGELENO_LOOKUP_LIVE=1` 及 `python -m her_agent.lookup --live --utterance ...`，语音通过原运行入口。Web开关优先于原 `ANGELENO_PLACE_TOOLS`，不会并行建立两套查询服务。该开关或文档不是调用授权。

[官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Search schema](https://api-docs.deepseek.com/zh-cn/api/search/)、[Fetch schema](https://api-docs.deepseek.com/zh-cn/api/fetch/)于2026-10-31核对：固定官方 `/v1/tools/search` 与 `/v1/tools/fetch`，有结果请求各CNY0.005，空结果／失败不收费。演员和新工具共用同一个Ledger及每请求余额检查；模型仍按token计费。工具取消、无有效响应或HTTP200无法解析时保留全额预留，不能按失败猜成免费。工具计数跨新adapter／进程按同一账本累计，达到批次上限停止，不新建账本重置。

本候选没有执行收费Search／Fetch；现有操作记录未明确覆盖它们。推荐首批合计至多6次、工具费至多¥0.03，模型费用另沿原许可／止损。批准及实际结果须接回原操作记录和#11，不能仅因产品范围或同API key就认为预算已批。

## 网络与数据边界

API请求只有固定官方目标，禁止重定向、不用环境代理；密钥不传给结果网站。Search只发query/count/need_content=false，不自动附ori_question、定位IP、作者稿或历史。查询词的语义最小化仍要在真实演员输出中检验，离线脚本不能证明模型永不携带多余信息。

Fetch只接受本轮已观察候选ID；当前保留原URL词法保护，只接受公开HTTPS、不含query/fragment/userinfo、非特殊端口的URL。未预登记网站可进入，私网／本机地址不行；在付费Fetch前检查本地解析地址，非公网或未知即拒绝，不为笔记本的DNS失败改安全门。没有对目标网站发送带密钥的请求。

**代理读取的实际限制**：本地DNS检查不是DeepSeek实际使用的解析，无法钉住其地址；Fetch返回仅title/content，不披露最终URL、跳转链、抓取时间或逐跳安全观察。官方提到安全拒绝403，但未给本项目可复验的逐跳明细。卡片／工具显式标注供应商转取与这些未知，不声称原直连读取器的逐跳证据已覆盖它。真实服务预跑与整体Review必须核该边界；本轮没有绕过任何私网或DNS拒绝。

## 可复验与仍欠

`python -m unittest discover -s tests -p test_web_lookup.py -v`：合成演员＋假HTTP验证新路径、现SDK合成播放和定向卡片、仅完整交付落库／不保存、日期改口原话关系、旧结果拒收、截断和错误、共享账本／计数／未知费用、危险URL／DNS拒绝。`make verify`另覆盖原全部门禁。它们不是实际模型选择、真实搜索资料、GPU音频或Owner接受。

2026-10-31 mini旧reader的4次实际NHM读取验证已收束：读取中取消与迟到隔离成立，改口丢原话关系被实读复现；本候选修该共享生命周期反例并离线验证，尚未重新做网络修后复验。原证据及SHA保留#11。

仍需固定候选独立Review、获准Search／Fetch最小实测及代理安全边界核验、同料比较条件冻结、真实GPU／浏览器语音和Owner判断。不能用脚本中的Jo台词或“已取得资料”卡片代替这些。
