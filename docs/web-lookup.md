# 主动发现与网页阅读候选

2026-10-31 Owner 明确选择主动发现新资料，替代仅预登记来源。Jo 的性格／成长经历影响关注、搜索和取舍；有效约定见 [Intent](intent.md) 与 [Spec](spec.md)，当前交付与接受见 [#1](https://github.com/OKFin33/Angeleno/issues/1)，[#11](https://github.com/OKFin33/Angeleno/issues/11) 保留已交付接合阶段证据。旧 reader 保留回退和历史证据，不继续扩站点提取器。

## 同一条运行路径

`ConversationRuntime → lookup.reply → WebLookup → DeepSeekWeb → Search / Fetch → ResultChannel → VoiceCards / 页面`。

演员可直接闲聊、查询记忆或稳定地方条目，也可选择公开地区／主题／地点ID，或直接提供明确公开的机构／场所名称、公开主题短语，由程序构造搜索词，再选择本轮返回的一个 `result_id` 阅读。新名称不必先泛搜取得ID。没有强制每轮联网或先问完偏好的脚本。每轮最多三次工具、四次模型请求；Web允许模型一次提出多项，整组先校验、逐项串行执行，Search／Fetch可重复但每项均占总数。稳定地方和记忆工具仍每类一次。每次Search最多五条、Fetch一页，外部等待合计10秒，无自动重试。重复调用ID或参数无效时，在任何工具派发前拒绝；合法超额批次按原顺序执行剩余额度内的前缀，其余明确未执行。部分普通失败保留对应失败结果，取消／过代或供应商安全拒绝后其余项明确返回未执行。两种网络动作接既有15／30秒保护，不等于真实语音延迟已通过。

人物稿只在已获准的对话模型上下文中影响关注与选择。Search没有人物稿、原始话语、理由、关系／历史、用户身份、联系方式或私人住址字段，也不接受完整自由query。公开机构名称由用户提到不自动成为用户个人信息；演员只提取必要规范名称／主题，不复述偏好原句或私人经历。未知地区不能塞进ID；不确定私人性质时不发送，简短说明或澄清，不因自称public就放行，也不直接照发整段网页标题。无需为新站写专用解析器。

## 发送边界：public-fields-v1

旧候选 `35441a9` 的query长度检查不能阻止私有字符串发送；`8edbca66` 改闭集构词并临时限制为精确合成demo包，`5e5bfa3` 用显式 `public-fields-v1` 替换包门禁。闭集是当时的工程防线，不撤回主动发现产品目标。本候选沿同一配置与PublicQuery扩展以下结构字段；配置须有已加载的AuthorPack，character／assistant共用同一路径，包正文不会传入Search适配器。原包和记忆不迁移，默认Web仍关闭。

API只接**精确类型** `PublicQuery`，在实际发送前再次验证所有字段。原area／topic／place_id枚举和原构词保持兼容，新增可选 `entity_name`（2—64字符，明确公开机构／场所规范名称，与place_id互斥）、`topic_phrase`（2—48字符，补充公开主题）。新实体构词为“地区＋实体名＋可选主题短语＋固定主题”；原地点ID可搭配主题短语。整句最多128字符，超限拒绝，不截断。工具只接这些字段及本地when／supplement；原始字符串、public标签、额外字段及未知ID在费用预留／HTTP前拒绝。Search载荷仍只有query、count、need_content=false，不传选择理由或原始上下文。

例如首次查询可指定Los Angeles Public Library、California African American Museum，或architecture下的adaptive reuse；small_art_exhibitions下可加opening reception。名称和短语不需预登记或先多调一次Search；这些例子不证明某活动存在、当前营业或模型能正确选择。语义最小化依赖演员已有上下文中的明确指令，**格式通过不证明内容公开、不含私人信息或已抵抗注入**。短语目前只接字母／汉字、空格及少量名称标点，每项最多10词；拒绝数字、常见联系方式／地址／日期、私人陈述和指令形式。这会误拒合法带数字或特殊符号的公开名称，也不能穷尽隐晦个人信息或改写后的诱导；不能把这些有限反例当完备隐私分类器。性质不明或格式不支持时保留限制，不编码绕过。

**日期仍不外发。** PublicQuery.day只为明确拒绝旧调用而保留，任何非None值都会失败，即使调用者绕过构造后篡改对象也在发送前拒绝。工具when只用于本地问题／卡片和取得资料后的事实判断，人物生日／历史时间／网页诱导日期都不能进入Search；新短语同样禁止日期，常见形式由程序拒绝，语义选择仍需演员遵守。公开ID／名称／主题的选择本身会透露检索兴趣，不声称消除了所有语义信号。

本地 `bind_local_date` 检查when能否对应本轮用户话语中的唯一日历日期，采用洛杉矶时区；窄范围的“我是说下周，不是这周”可从仍可修订的原查询继承日期并加七天。没有日期、无法解析、多个日期、明确否定／未定、区间（含省略第二个月份）或与当前话语不匹配时保持未知，并把date_binding和date_sent=false返回演员。未知日期仍可取得不含日期的资料，不能声称具体日期已确认。这是来源关联检查加保守展示保护，**不是语义理解器**：提及某日不自动证明想在该日出行，演员仍须理解否定／选择／条件并在必要时澄清。日期缺省不从人物或任意旧历史推断。

取得资料标为evidence、conclusion为空；搜索摘要与正文不是已核实事实。发布日期、读取时刻和适用日期分开，缺字段不猜。正文超4000字符返回截断／信息不足；HTTP响应限128KiB。网页文字作为不可信tool数据，不是权限或操作指令；真实模型是否正确识别冲突／抵抗注入仍待情景验证。

显式取消结束查询关系；插话中断取消旧请求，后继supplement保留原始话语并增加版本。旧请求不能发布、播报或写成已完成交流。已读证据只有实际完整交付时才沿原记忆入口留存；临时“不保存”模式不落盘。

## 运行开关与计费

实际端点和数据载荷获准后，操作者在进程中显式设置 `ANGELENO_WEB_ENABLED=1`、`ANGELENO_WEB_EGRESS=public-fields-v1` 及 `ANGELENO_WEB_REQUEST_LIMIT`（显式正整数、原账本累计总数），继续用原配置和统一账本。续批须有当前有效许可，累计上限包含旧请求，不清零历史；原首批6次是历史授权，不是永久代码上限。每轮3工具／4模型及累计Web等待10秒保护不变。CLI沿已有 `ANGELENO_LOOKUP_LIVE=1` 与 `python -m her_agent.lookup --live --utterance ...`，语音沿原入口。Web优先于ANGELENO_PLACE_TOOLS，不并行建两套查询服务；开关和计数上限本身不是授权。

当当前授权是按账户余额合理使用、没有累计次数封顶时，显式选择 `ANGELENO_WEB_REQUEST_LIMIT=account`。
该模式不再用历史批次计数阻止新调用；旧行、未知费用、余额止损及每次新余额核查全部保留。
整数模式仍用于确有次数限制的授权，默认空值仍拒绝。不能把 `account` 当作充值、扩大ASR/GPU额度或越过供应商403的许可。
完整私有语音试用沿[试运行说明](trial.md#启动与停止)，真实Search/Fetch与卡片使用同一个ConversationRuntime。

[官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Search schema](https://api-docs.deepseek.com/zh-cn/api/search/)、[Fetch schema](https://api-docs.deepseek.com/zh-cn/api/fetch/)于2026-10-31核对：固定官方 `/v1/tools/search` 与 `/v1/tools/fetch`，有结果请求各CNY0.005，空结果／失败不收费。演员和工具共用Ledger及每请求余额检查，模型仍按token计费。取消、响应未知或HTTP200无法解析保留全额预留，不猜免费；计数跨adapter／进程累计，不用新账本重置。

以下为历史首批许可与当时检查点，不代表当前剩余额度：Owner当时批准仅公开测试查询／URL，合计最多6次／¥0.03。实际记录：

- 14:00Z，`35441a9`：一次Search成功，5条结果，约0.502秒；Fetch在本地DNS预检拒绝，未发请求。旧回执未留底层分类，不能事后推断原因。
- 14:47Z，已审 `bfc2ebfd`：一次公开建筑／空间Search成功，约1.573秒，经现有URL规则得到3个候选；选本轮首个China Daily网页，Fetch在约0.006秒预检返回 `candidate_dns_refused:non_public`，未进入HTTP或费用预留，无正文。该分类来自应用，不是工具审批拒绝或供应商403。没有换目标／DNS／代理／机器重试。
- 累计实际端点请求2/6，均为成功Search，账本估算¥0.01/¥0.03、无未知工具费用；余4次／¥0.02仍属原批次。本轮已按预检拒绝停止，不自动用完。无新增模型调用。

第二次脱敏回执 `tmp/web-discovery/public-receipt-v2.json` SHA256 `de130512668ce1c07db18da3b716a723b52d91d6940cabc0f9ff87a37d679cd3`；完整账本与许可由ignored操作者记录持有。搜索标题／摘要不是当前事实确认，两次公开手工选择也不证明Jo真实模型的自主取舍。

## 网络与代理边界

HTTP只连接固定DeepSeek官方HTTPS API，保留TLS、禁止API重定向、不用环境代理；密钥不传给候选网站。Fetch只接受本轮候选ID；URL词法拒绝非HTTPS、非公网IP字面量、模糊数值IP（先规范化DNS尾点）、本机／内部名、危险编码（当前保守拒所有百分号编码）、query／fragment／userinfo和特殊端口。没有直接向目标站发送带密钥请求。

**2026-10-31架构审阅修正**：本机解析候选域名所得地址没有用于DeepSeek Fetch的连接。把它作为远端API硬门槛，既不能验证供应商远端地址，也会误阻请求；因此本后继移除此处的强制本机DNS。不是临时换DNS或关闭直连保护。原LiveReader仍连接目标，PublicResolver校验的同一地址列表交给连接器，URL逐跳检查／TLS／体积与取消保护全部保留。

DeepSeek承担实际目标解析、连接和转取跳转。[Fetch schema](https://api-docs.deepseek.com/api/fetch/)仅提供title/content，[官方指南](https://api-docs.deepseek.com/guides/search/)列出403安全策略拒绝；没有最终URL、跳转链、抓取时间或可供本地复验的逐跳证明。这是明确保留的供应商信任边界，不声称移除本机DNS后远端安全已被证明。模型payload与页面继续展示这一限制。供应商Fetch返回403时停止本批Web；该拒绝保留原账本，此后启动的Search／Fetch以及重建适配器在新余额查询、预留和HTTP前停止；已在等待余额的请求会在预留后、派发前再次读取拒绝状态，若被阻止则将未发送预留结算为已知零费用。不能自动换网址／供应商重试。没有新增解除开关或重置账本路径。当前真实验证严格单路串行；这不是跨进程原子派发保证，不能追回已发出的并发请求。

历史两次DNS拒绝回执不变：14:47只留non_public大类，没有保存地址或更细类别，不能分辨RFC1918、环回、保留测试网段或fake-IP。14:00甚至没有底层分类。它们不证明网站不安全。public_dns仅留作原诊断复现入口，运行路径不调用；本轮没有新增解析、网络扫描、代理读取或配置修改。只读系统配置的PAC与nameserver观察不能判定根因。

## 可复验与仍欠

`python -m unittest discover -s tests -p test_web_lookup.py -v`、`test_public_dates.py`及 `make verify`覆盖合成演员／假HTTP的共享工具与记忆、当前卡片、改口／取消／旧结果、错误／截断、账本／计数／未知费用、URL／DNS拒绝。新增回归让演员看见合成私有包，经真实适配器和假HTTP走三轮Search→Fetch：私人生日、当前用户日期、恶意正文诱导旧日期；每次出站只有固定公开词或本轮URL，当前用户日期仅留本地。还验证构造后篡改日期在发送前拒绝、LA跨年日期关联，以及六次累计后第七次拒绝。模型输出仍是预写合成数据。

2026-10-31 mini旧reader的4次NHM真实读取已收束：取消／迟到隔离成立，改口丢原话被实读复现；共享生命周期已修并离线验证，未网络重跑。旧证据保留#11。

**历史检查点（2026-10-31，不是当前下一步或剩余额度）：** 独立审阅本架构修正后，再依据原许可／累计账本恢复有界API观察；本轮没有重试旧目标或新增工具HTTP。候选ID、URL词法、官方API与费用检查必须通过，供应商403立即停批，费用未知保留预留。现有4次／¥0.02不重置或自动变成新批次。模型回放可单独使用既有公开Search摘要和原DNS失败记录，但必须标明历史回放、无正文，不能冒充实时Fetch。当时尚缺真实Fetch正文／供应商安全观察、完整Jo行为、同料比较冻结、GPU／浏览器音频及Owner接受；后续证据及仍未验范围见 [trial](trial.md)。

本架构回归用假HTTP证明本机DNS无论返回保留地址或解析失败都不会被远端Fetch调用；实际请求只有官方余额和Fetch API。另证危险编码／私网字面量等在费用前拒绝、API302不跟随、供应商403关闭响应并跨适配器停止后续请求、取消继续保留未知费用。原直连DNS同组地址／混合私网拒绝／逐跳回归仍在完整门禁中。它们不构成供应商内部安全机制的实测证明。

PR25后续独立审阅补回两个反例：尾点在数值主机检查前规范化，标准／八进制／十六进制／混合形式及尾点均按词法拒绝；另用真实适配器＋假HTTP重现B等待余额期间A记录403，B恢复后不发POST、未发送预留以零结算。原cad96394保留审计，不把其测试通过当作上述漏洞当时不存在。

## 历史观察与计划日期的接合

同轮 Search 已取得候选、实际选中的 Fetch 随后失败／超时时，卡片保留该候选所属的
Search 批次引用，状态仍是 `unavailable`、结论为空。引用保持 `search_summary`，
页面明确显示“搜索摘要，页面未读到／未核验”，不能作为已读正文或已确认事实。
Fetch 成功仅将实际所选 `result_id` 的正文列为 `fetched_page`；缺来源不借用旧卡，
取消、换轮、身份变化及补充条件过代均不能恢复旧引用。没有额外查询或预算扩张。

沿试运行入口可选的 `ANGELENO_DIAGNOSTICS_FILE` 私有记录，新增实际选择的公开
`result_id`／URL、HTTPX JSON 请求体 SHA256、开始／结束时间、剩余等待预算及取消类别。
选择记录和实际 HTTP 派发记录分别保留；没有 HTTP 开始记录不能声称已发送。
不记录自由查询、原对白、请求头或正文；默认关闭，不上传诊断文件。
旧回执没记录的参数和时间仍未知，不从候选链接或其他文件 hash 补造。

真实Jo回放曾把未覆盖查询的 `insufficient + results:[]` 理解为实际搜索无命中，
并把摘要中的计划开放日期推成已开业时长。回放说明不能替代每条工具结果的准确状态。

只对有精确query／URL、时间和hash匹配的资料，回放适配器返回
`RecordedWebResult(data, recorded_at, source_sha256)`；已观察到的空搜索仍是
`data={"results": []}`。没有匹配历史观察时返回 `RecordedWebResult(None, None, source_sha256)`，
不是空结果或真实HTTP失败。WebLookup将其报告为unavailable及
`replay_query_uncovered`／`replay_body_unobserved`，模型可见coverage、匹配观察时间和live_http=false。
这些元数据经生产reconcile保留；供应商原始JSON中的同名字段不会自行获得回放身份。
原临时验证器须改用这个返回类型，不能继续先返回空数组再补一个replay标签。
正式比较CorpusWeb的未覆盖即停规则不变。

`published_at_kind=source_publication`明确文章时间；`event_occurrence=not_established_by_tool`
不认证事件已发生。原文和计划措辞原样保留，共用运行提示明确：日期已经过去也不能将
“计划／预计／will open”升格为实际开业或计算开业多久。没有自动改答案、指定候选或改人物稿。
离线回归只证明状态／字段／提示送达模型，不能证明模型已纠正；实际效果须原账本串行回归。

每条工具结果的 `evidence_reading` 单独给出观察范围与可自然转述的 `spoken_scope`：
历史查询未覆盖、历史查询已观察为空、本次实时查询没有可用候选分别表达。
适配器来源未核时不冒充实时搜索；这些范围通过原有tool消息进入下一次actor请求，不另加判读模型。

回放适配器可在第四参数提供显式阅读标注：
`RecordedWebResult(data, recorded_at, source_sha256, assertions=(... ,))`。
每个 `SourceAssertion(event, modality, field, start, end, quote, result_index)` 绑定当前可见
`summary`（需原始结果序号）或 `content`（序号为None）的Python字符区间 `[start:end]`。
例如 `media_preview / reported_actual` 与 `public_opening / planned` 是不同来源断言，
媒体预览报道不能替代公众开放证据，过去的计划不因日期已过而升级。只在截取后的可见文本中逐字匹配
才保留；过滤掉的候选、截断外或不匹配的标注仅计入unbound，不将隐藏quote送给模型。
标注表达阅读者对原文的解释，`fact_verification=not_performed`；不认证事实真值、适用日期或当前开放。
原始供应商JSON不能自行注入这些标注，网页指令留在tool数据中，不插入system。

这不是自动语义分类器。线上结果没有显式阅读标注时 `source_assertions=[]`，仍须阅读原文并允许未知；
本轮未增加付费判读阶段、未改工具选择、四次模型上限或15／30秒首音保护。
`examples/comparison/evidence-strength.development.json` 的六个公开合成反例经原有续答路径验证接线，
不是保留集或模型得分。原真实回放只有计划公众开放与已发生媒体预览，不能让“已确认公众开放”通过；
它仍足以支持有条件的建筑取舍。实际口语修正与线上无标注效果均未验证。

## 串行批次的剩余额度

每轮最多三次工具执行、四次actor请求不变。整批参数／ID先预验，合法批次再按模型原提案顺序
执行额度内的前缀，不按结果好坏选工具。超额项逐tool_call_id返回not_executed、
tool_limit_reached和空conclusion，不派发、不预留费用、不伪造结果或卡片；后继actor不再获工具。
取消、拒绝和供应商403优先停止后续工具并保留各自原因；余额不足仍立即终止，不加模型请求。
若模型在工具已关闭后仍提出有效调用，也只回未执行信息；到第四次actor请求不发第五次，
只给明确的停止核对说明，不补造事实答案。未执行项不是搜索空结果。
