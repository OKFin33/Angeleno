# 主动发现与网页阅读候选

2026-10-31 Owner 明确选择主动发现新资料，替代仅预登记来源。Jo 的性格／成长经历影响关注、搜索和取舍；有效约定见 [Intent](intent.md) 与 [Spec](spec.md)，实现与真实证据回 [#11](https://github.com/OKFin33/Angeleno/issues/11)。旧 reader 保留回退和历史证据，不继续扩站点提取器。

## 同一条运行路径

`ConversationRuntime → lookup.reply → WebLookup → DeepSeekWeb → Search / Fetch → ResultChannel → VoiceCards / 页面`。

演员可直接闲聊、查询记忆或稳定地方条目，也可选择公开地区／主题／地点ID，由程序构造搜索词，再选择本轮返回的一个 `result_id` 阅读。没有强制每轮联网或先问完偏好的脚本。每轮最多三次工具、每类一次、四次模型请求；Search最多五条，Fetch一页，外部等待合计10秒，无自动重试。两种网络动作接既有15／30秒保护，不等于真实语音延迟已通过。

人物稿只在已获准的对话模型上下文中影响关注与选择。Search不能接人物稿、用户话语、理由、关系／历史、身份、联系方式、住址或任意自由query。固定公开主题可表达建筑、社区空间或小展等不同兴趣；网站由搜索发现，并非仍限定两个登记源。未知地区／主题不能塞进其他字段，需如实说明当前覆盖不足，不为新站写专用解析器。

## 发送边界：public-fields-v1

旧候选 `35441a9` 的query长度检查不能阻止私有字符串发送；`8edbca66` 改闭集构词并临时限制为精确合成demo包。本后继把临时包门禁替换为显式 `public-fields-v1` 发送策略：配置须有已加载的AuthorPack，character／assistant共用同一路径，包正文不会传入Search适配器。原包和记忆不迁移，默认Web仍关闭。

API只接**精确类型** `PublicQuery`，在实际发送前再次验证所有字段。地区、主题、可选地点只能来自源码内公开枚举；HTTP Search载荷只含程序构造的query、count、need_content=false。原始字符串、伪公开标签、附加自由字段和未知ID在发送与费用预留前拒绝，不靠关键词过滤。当前枚举覆盖Jefferson Park／USC–Exposition Park、展览／建筑／公共空间等主题及Fisher／NHM；它约束的是外发意图，不是网页域名。

**所有日期均不外发。** PublicQuery.day只为明确拒绝旧调用而保留，任何非None值都会失败，即使调用者绕过构造后篡改对象也在发送前拒绝。工具when只用于本地问题／卡片和取得资料后的事实判断，人物生日／历史时间／网页诱导日期都不能进入Search。公开ID选择本身仍会透露主题兴趣，不声称完全消除了所有语义信号。

本地 `bind_local_date` 检查when能否对应本轮用户话语中的唯一日历日期，采用洛杉矶时区；窄范围的“我是说下周，不是这周”可从仍可修订的原查询继承日期并加七天。没有日期、无法解析、多个日期、明确否定／未定、区间（含省略第二个月份）或与当前话语不匹配时保持未知，并把date_binding和date_sent=false返回演员。未知日期仍可取得不含日期的资料，不能声称具体日期已确认。这是来源关联检查加保守展示保护，**不是语义理解器**：提及某日不自动证明想在该日出行，演员仍须理解否定／选择／条件并在必要时澄清。日期缺省不从人物或任意旧历史推断。

取得资料标为evidence、conclusion为空；搜索摘要与正文不是已核实事实。发布日期、读取时刻和适用日期分开，缺字段不猜。正文超4000字符返回截断／信息不足；HTTP响应限128KiB。网页文字作为不可信tool数据，不是权限或操作指令；真实模型是否正确识别冲突／抵抗注入仍待情景验证。

显式取消结束查询关系；插话中断取消旧请求，后继supplement保留原始话语并增加版本。旧请求不能发布、播报或写成已完成交流。已读证据只有实际完整交付时才沿原记忆入口留存；临时“不保存”模式不落盘。

## 运行开关与计费

实际端点和数据载荷获准后，操作者在进程中显式设置 `ANGELENO_WEB_ENABLED=1`、`ANGELENO_WEB_EGRESS=public-fields-v1` 及 `ANGELENO_WEB_REQUEST_LIMIT`（首批1–6），继续用原配置和统一账本。CLI沿已有 `ANGELENO_LOOKUP_LIVE=1` 与 `python -m her_agent.lookup --live --utterance ...`，语音沿原入口。Web优先于ANGELENO_PLACE_TOOLS，不并行建两套查询服务；开关本身不是授权。本次没有修改操作者运行配置或发送实际Jo新搜索。

[官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Search schema](https://api-docs.deepseek.com/zh-cn/api/search/)、[Fetch schema](https://api-docs.deepseek.com/zh-cn/api/fetch/)于2026-10-31核对：固定官方 `/v1/tools/search` 与 `/v1/tools/fetch`，有结果请求各CNY0.005，空结果／失败不收费。演员和工具共用Ledger及每请求余额检查，模型仍按token计费。取消、响应未知或HTTP200无法解析保留全额预留，不猜免费；计数跨adapter／进程累计，不用新账本重置。

Owner批准的本批仅公开测试查询／URL，合计最多6次／¥0.03。实际记录：

- 14:00Z，`35441a9`：一次Search成功，5条结果，约0.502秒；Fetch在本地DNS预检拒绝，未发请求。旧回执未留底层分类，不能事后推断原因。
- 14:47Z，已审 `bfc2ebfd`：一次公开建筑／空间Search成功，约1.573秒，经现有URL规则得到3个候选；选本轮首个China Daily网页，Fetch在约0.006秒预检返回 `candidate_dns_refused:non_public`，未进入HTTP或费用预留，无正文。该分类来自应用，不是工具审批拒绝或供应商403。没有换目标／DNS／代理／机器重试。
- 累计实际端点请求2/6，均为成功Search，账本估算¥0.01/¥0.03、无未知工具费用；余4次／¥0.02仍属原批次。本轮已按预检拒绝停止，不自动用完。无新增模型调用。

第二次脱敏回执 `tmp/web-discovery/public-receipt-v2.json` SHA256 `de130512668ce1c07db18da3b716a723b52d91d6940cabc0f9ff87a37d679cd3`；完整账本与许可由ignored操作者记录持有。搜索标题／摘要不是当前事实确认，两次公开手工选择也不证明Jo真实模型的自主取舍。

## 网络与代理边界

HTTP只连接固定DeepSeek官方API，TLS、禁止API重定向、不用环境代理；密钥不传给结果网站。Fetch只接受本轮候选ID，URL须为公开HTTPS，不含query／fragment／userinfo／特殊端口；付费前正常DNS检查，私网／本机／未知均拒绝。没有直接向目标站发送带密钥请求。

DNS固定分类为resolver_os_error、empty、non_public、invalid_answer，不保存原异常或地址；混合公网私网与畸形答案仍拒绝，取消向上传递。14:47拒绝只证明该次应用解析未满足全公网要求，不确定原因。只读主机配置见公共nameserver与PAC启用，不能证明PAC因果、没有透明网络层，或供应商解析安全；没有读取PAC内容或修改配置。旧14:00回执保持原样，不补造分类。

**本地DNS不代表DeepSeek实际解析**，无法钉住供应商地址。Fetch返回title/content，不披露最终URL、跳转链、抓取时间或逐跳安全观察。模型payload保留provenance；页面来源展开显示“尚未读取正文”或“由搜索服务读取；最终地址和跳转过程未提供”。不能把原直连reader的逐跳证据套到代理Fetch。供应商安全边界与真实正文均未验收。

## 可复验与仍欠

`python -m unittest discover -s tests -p test_web_lookup.py -v`、`test_public_dates.py`及 `make verify`覆盖合成演员／假HTTP的共享工具与记忆、当前卡片、改口／取消／旧结果、错误／截断、账本／计数／未知费用、URL／DNS拒绝。新增回归让演员看见合成私有包，经真实适配器和假HTTP走三轮Search→Fetch：私人生日、当前用户日期、恶意正文诱导旧日期；每次出站只有固定公开词或本轮URL，当前用户日期仅留本地。还验证构造后篡改日期在发送前拒绝、LA跨年日期关联，以及六次累计后第七次拒绝。模型输出仍是预写合成数据。

2026-10-31 mini旧reader的4次NHM真实读取已收束：取消／迟到隔离成立，改口丢原话被实读复现；共享生命周期已修并离线验证，未网络重跑。旧证据保留#11。

下一步：本发送策略独立Review；以原许可／累计账本核实际公开载荷，取得Jo实模选择与可安全Fetch正文的观察，再接真实媒体。后续尝试须是获准当前查询的真实候选，原URL与普通DNS均通过；任一预检拒绝或费用不明即停，不盲重放旧目标或改网络绕过。现有4次／¥0.02余额不自动授权新的批次，模型沿原账本另核。当前仍缺真实Fetch正文／供应商安全观察、完整Jo行为、同料比较冻结、GPU／浏览器音频及Owner接受。
