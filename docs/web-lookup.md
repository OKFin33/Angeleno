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

HTTP只连接固定DeepSeek官方HTTPS API，保留TLS、禁止API重定向、不用环境代理；密钥不传给候选网站。Fetch只接受本轮候选ID；URL词法拒绝非HTTPS、非公网IP字面量、模糊数值IP、本机／内部名、危险编码（当前保守拒所有百分号编码）、query／fragment／userinfo和特殊端口。没有直接向目标站发送带密钥请求。

**2026-10-31架构审阅修正**：本机解析候选域名所得地址没有用于DeepSeek Fetch的连接。把它作为远端API硬门槛，既不能验证供应商远端地址，也会误阻请求；因此本后继移除此处的强制本机DNS。不是临时换DNS或关闭直连保护。原LiveReader仍连接目标，PublicResolver校验的同一地址列表交给连接器，URL逐跳检查／TLS／体积与取消保护全部保留。

DeepSeek承担实际目标解析、连接和转取跳转。[Fetch schema](https://api-docs.deepseek.com/api/fetch/)仅提供title/content，[官方指南](https://api-docs.deepseek.com/guides/search/)列出403安全策略拒绝；没有最终URL、跳转链、抓取时间或可供本地复验的逐跳证明。这是明确保留的供应商信任边界，不声称移除本机DNS后远端安全已被证明。模型payload与页面继续展示这一限制。供应商Fetch返回403时停止本批Web；该拒绝保留原账本，新的Search／Fetch以及重建适配器均在新的余额查询、预留和HTTP前停止，不能自动换网址／供应商重试。没有新增解除开关或重置账本路径。

历史两次DNS拒绝回执不变：14:47只留non_public大类，没有保存地址或更细类别，不能分辨RFC1918、环回、保留测试网段或fake-IP。14:00甚至没有底层分类。它们不证明网站不安全。public_dns仅留作原诊断复现入口，运行路径不调用；本轮没有新增解析、网络扫描、代理读取或配置修改。只读系统配置的PAC与nameserver观察不能判定根因。

## 可复验与仍欠

`python -m unittest discover -s tests -p test_web_lookup.py -v`、`test_public_dates.py`及 `make verify`覆盖合成演员／假HTTP的共享工具与记忆、当前卡片、改口／取消／旧结果、错误／截断、账本／计数／未知费用、URL／DNS拒绝。新增回归让演员看见合成私有包，经真实适配器和假HTTP走三轮Search→Fetch：私人生日、当前用户日期、恶意正文诱导旧日期；每次出站只有固定公开词或本轮URL，当前用户日期仅留本地。还验证构造后篡改日期在发送前拒绝、LA跨年日期关联，以及六次累计后第七次拒绝。模型输出仍是预写合成数据。

2026-10-31 mini旧reader的4次NHM真实读取已收束：取消／迟到隔离成立，改口丢原话被实读复现；共享生命周期已修并离线验证，未网络重跑。旧证据保留#11。

下一步：独立审阅本架构修正后，再依据原许可／累计账本恢复有界API观察；本轮没有重试旧目标或新增工具HTTP。候选ID、URL词法、官方API与费用检查必须通过，供应商403立即停批，费用未知保留预留。现有4次／¥0.02不重置或自动变成新批次。模型回放可单独使用既有公开Search摘要和原DNS失败记录，但必须标明历史回放、无正文，不能冒充实时Fetch。当前仍缺真实Fetch正文／供应商安全观察、完整Jo行为、同料比较冻结、GPU／浏览器音频及Owner接受。

本架构回归用假HTTP证明本机DNS无论返回保留地址或解析失败都不会被远端Fetch调用；实际请求只有官方余额和Fetch API。另证危险编码／私网字面量等在费用前拒绝、API302不跟随、供应商403关闭响应并跨适配器停止后续请求、取消继续保留未知费用。原直连DNS同组地址／混合私网拒绝／逐跳回归仍在完整门禁中。它们不构成供应商内部安全机制的实测证明。
