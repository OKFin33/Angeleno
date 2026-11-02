# Web 查询与来源卡片

Jo 可按当前话题搜索公开资料、阅读候选网页，并以来源卡片展示依据。人物背景与偏好影响她的关注与取舍；工具服务交流，普通闲聊无需联网。产品行为见 [Spec](spec.md)，配置见 [running](running.md)。

## 工具与执行限额

`ConversationRuntime → lookup.reply → WebLookup → DeepSeekWeb → Search / Fetch → ResultChannel → VoiceCards / 页面`。

演员可直接闲聊、查询记忆或稳定地方条目，也可选择公开地区／主题／地点ID，或直接提供明确公开的机构／场所名称、公开主题短语，由程序构造搜索词，再选择本轮返回的一个 `result_id` 阅读。新名称不必先泛搜取得ID。没有强制每轮联网或先问完偏好的脚本。每轮最多三次工具、四次模型请求；Web允许模型一次提出多项，整组先校验、逐项串行执行，Search／Fetch可重复但每项均占总数。稳定地方和记忆工具仍每类一次。每次Search最多五条、Fetch一页，外部等待合计10秒，无自动重试。重复调用ID或参数无效时，在任何工具派发前拒绝；合法超额批次按原顺序执行剩余额度内的前缀，其余明确未执行。部分普通失败保留对应失败结果，取消／过代或供应商安全拒绝后其余项明确返回未执行。语音轮次同时受15／30秒首音保护约束。

人物稿在对话模型上下文中影响关注与选择。Search没有人物稿、原始话语、理由、关系／历史、用户身份、联系方式或私人住址字段，也不接受完整自由query。公开机构名称由用户提到不自动成为用户个人信息；演员只提取必要规范名称／主题，不复述偏好原句或私人经历。未知地区不能塞进ID；不确定私人性质时不发送，简短说明或澄清，不因自称public就放行，也不直接照发整段网页标题。无需为新站写专用解析器。

## 发送边界：public-fields-v1

查询使用结构化 `PublicQuery`，人物与普通助手角色共用同一路径。配置需加载 AuthorPack；包正文留在对话模型上下文中，不传入 Search 适配器。Web 默认关闭，启用方式见下节。

API只接**精确类型** `PublicQuery`，在实际发送前再次验证所有字段。支持 area／topic／place_id 枚举，以及可选 `entity_name`（2—64字符，明确公开机构／场所规范名称，与place_id互斥）、`topic_phrase`（2—48字符，补充公开主题）。新实体构词为“地区＋实体名＋可选主题短语＋固定主题”；原地点ID可搭配主题短语。整句最多128字符，超限拒绝，不截断。工具只接这些字段及本地when／supplement；原始字符串、public标签、额外字段及未知ID在费用预留／HTTP前拒绝。Search载荷仍只有query、count、need_content=false，不传选择理由或原始上下文。

例如首次查询可指定Los Angeles Public Library、California African American Museum，或architecture下的adaptive reuse；small_art_exhibitions下可加opening reception。名称和短语无需预登记。演员负责从上下文中选择最少必要的公开意图；程序执行格式和字段检查。短语目前只接字母／汉字、空格及少量名称标点，每项最多10词；拒绝数字、常见联系方式／地址／日期、私人陈述和指令形式。因此，带数字或特殊符号的合法公开名称可能被拒绝。格式检查无法完整判断隐晦个人信息或诱导，性质不明时应澄清，格式不支持时保留限制。

**日期仍不外发。** PublicQuery.day只为明确拒绝旧调用而保留，任何非None值都会失败，即使调用者绕过构造后篡改对象也在发送前拒绝。工具when只用于本地问题／卡片和取得资料后的事实判断，人物生日／历史时间／网页诱导日期都不能进入Search；新短语同样禁止日期，常见形式由程序拒绝，语义选择仍需演员遵守。公开ID／名称／主题仍会向供应商透露本次检索意图。

本地 `bind_local_date` 检查when能否对应本轮用户话语中的唯一日历日期，采用洛杉矶时区；窄范围的“我是说下周，不是这周”可从仍可修订的原查询继承日期并加七天。没有日期、无法解析、多个日期、明确否定／未定、区间（含省略第二个月份）或与当前话语不匹配时保持未知，并把date_binding和date_sent=false返回演员。未知日期仍可取得不含日期的资料，不能声称具体日期已确认。该检查关联日期来源；出行意图、否定与条件仍由演员结合当前话语理解，必要时澄清。日期缺省不从人物或任意旧历史推断。

取得资料标为evidence、conclusion为空；搜索摘要与正文不是已核实事实。发布日期、读取时刻和适用日期分开，缺字段不猜。正文超4000字符返回截断／信息不足；HTTP响应限128KiB。网页文字作为不可信 tool 数据处理；模型仍可能误读冲突或页面指令，具体情景验证见 [trial](trial.md)。

显式取消结束查询关系；插话中断取消旧请求，后继supplement保留原始话语并增加版本。旧请求不能发布、播报或写成已完成交流。已读证据只有实际完整交付时才沿原记忆入口留存；临时“不保存”模式不落盘。

## 运行开关与计费

在自己的配置中设置：

```dotenv
ANGELENO_WEB_ENABLED=1
ANGELENO_WEB_EGRESS=public-fields-v1
ANGELENO_WEB_REQUEST_LIMIT=account
```

`account` 按账户余额和每轮限额运行；也可设置正整数作为原账本中的累计请求上限。
两种模式都保留历史用量、未知费用与每请求余额检查，空值拒绝启动。
Web 优先于 `ANGELENO_PLACE_TOOLS`；语音沿 [running](running.md) 的启动入口，
文字工具入口使用 `ANGELENO_LOOKUP_LIVE=1` 与 `python -m her_agent.lookup --live --utterance ...`。
Search／Fetch、回复与卡片共用同一个 ConversationRuntime。

[官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Search schema](https://api-docs.deepseek.com/zh-cn/api/search/)、[Fetch schema](https://api-docs.deepseek.com/zh-cn/api/fetch/)于2026-10-31核对：固定官方 `/v1/tools/search` 与 `/v1/tools/fetch`，有结果请求各CNY0.005，空结果／失败不收费。演员和工具共用Ledger及每请求余额检查，模型仍按token计费。取消、响应未知或HTTP200无法解析保留全额预留，不猜免费；计数跨adapter／进程累计，不用新账本重置。

## 网络与代理边界

HTTP只连接固定DeepSeek官方HTTPS API，保留TLS、禁止API重定向、不用环境代理；密钥不传给候选网站。Fetch只接受本轮候选ID；URL词法拒绝非HTTPS、非公网IP字面量、模糊数值IP（先规范化DNS尾点）、本机／内部名、危险编码（当前保守拒所有百分号编码）、query／fragment／userinfo和特殊端口。没有直接向目标站发送带密钥请求。

DeepSeek Fetch 在供应商端读取网页，本机候选域名 DNS 不参与其连接，也不作为发送门槛。可选直连 LiveReader 则使用 PublicResolver 校验的同组地址连接目标，并逐跳检查 URL、TLS、体积与取消。两者的网络观察位置不同。

DeepSeek承担实际目标解析、连接和转取跳转。[Fetch schema](https://api-docs.deepseek.com/api/fetch/)仅提供title/content，[官方指南](https://api-docs.deepseek.com/guides/search/)列出403安全策略拒绝；没有最终URL、跳转链、抓取时间或可供本地复验的逐跳证明。这些字段保持未知，并在模型 payload 与页面展示。供应商Fetch返回403时停止本批Web；该拒绝保留原账本，此后启动的Search／Fetch以及重建适配器在新余额查询、预留和HTTP前停止；已在等待余额的请求会在预留后、派发前再次读取拒绝状态，若被阻止则将未发送预留结算为已知零费用。后续请求保持停止，不能通过换网址或重建适配器解除。该检查不是跨进程原子锁，已派发的并发请求无法撤回。

## 卡片与证据状态

同轮 Search 已取得候选、实际选中的 Fetch 随后失败／超时时，卡片保留该候选所属的
Search 批次引用，状态仍是 `unavailable`、结论为空。引用保持 `search_summary`，
页面明确显示“搜索摘要，页面未读到／未核验”，不能作为已读正文或已确认事实。
Fetch 成功仅将实际所选 `result_id` 的正文列为 `fetched_page`；缺来源不借用旧卡，
取消、换轮、身份变化及补充条件过代均不能恢复旧引用。

可选的 `ANGELENO_DIAGNOSTICS_FILE` 私有记录包含实际选择的公开
`result_id`／URL、HTTPX JSON 请求体 SHA256、开始／结束时间、剩余等待预算及取消类别。
选择记录和实际 HTTP 派发记录分别保留；没有 HTTP 开始记录不能声称已发送。
这些 Web 元数据事件不含自由查询、原对白、请求头或正文。另行启用的逐轮文本 trace 会记录更完整的对话与工具内容，见 [trace 配置](running.md#private-per-turn-trace)。

### 开发回放

回放结果必须区分实际空结果与语料未覆盖。

只对有精确query／URL、时间和hash匹配的资料，回放适配器返回
`RecordedWebResult(data, recorded_at, source_sha256)`；已观察到的空搜索仍是
`data={"results": []}`。没有匹配历史观察时返回 `RecordedWebResult(None, None, source_sha256)`，
不是空结果或真实HTTP失败。WebLookup 将缺失观察标为 `not_observed`，带有
`replay_query_uncovered`／`replay_body_unobserved` 和 `live_http=false`。工具循环在模型续答前
抛出 `LookupObservationMissing`，保留待处理输入，供开发者补全语料。已有观察保留 coverage 与匹配时间。
适配器来源通过 reconcile 保留；供应商原始 JSON 中的同名字段不会自行获得回放身份。
开发回放工具应使用该返回类型。CorpusWeb 在未覆盖时停止比较，不将空数组冒充实际观察。

`published_at_kind=source_publication`明确文章时间；`event_occurrence=not_established_by_tool`
不认证事件已发生。原文和计划措辞原样保留，共用运行提示明确：日期已经过去也不能将
“计划／预计／will open”升格为实际开业或计算开业多久。

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

线上结果没有显式阅读标注时 `source_assertions=[]`；演员结合可见原文判断，并保留未知。
`examples/comparison/evidence-strength.development.json` 提供六个合成开发反例，用于检查证据等级的接合；正式比较的样本与评分见 [comparison](comparison.md)。

## 串行批次的剩余额度

每轮最多三次工具执行、四次actor请求不变。整批参数／ID先预验，合法批次再按模型原提案顺序
执行额度内的前缀，不按结果好坏选工具。超额项逐tool_call_id返回not_executed、
tool_limit_reached和空conclusion，不派发、不预留费用、不伪造结果或卡片；后继actor不再获工具。
取消、拒绝和供应商403优先停止后续工具并保留各自原因；余额不足仍立即终止，不加模型请求。
若模型在工具已关闭后仍提出有效调用，也只回未执行信息；到第四次actor请求不发第五次，
只给明确的停止核对说明，不补造事实答案。未执行项不是搜索空结果。

## 历史验证记录

<details>
<summary>2026-10-31 请求回执与实现检查点</summary>

以下为历史首批许可与当时检查点，不代表当前剩余额度：Owner当时批准仅公开测试查询／URL，合计最多6次／¥0.03。实际记录：

- 14:00Z，`35441a9`：一次Search成功，5条结果，约0.502秒；Fetch在本地DNS预检拒绝，未发请求。旧回执未留底层分类，不能事后推断原因。
- 14:47Z，已审 `bfc2ebfd`：一次公开建筑／空间Search成功，约1.573秒，经现有URL规则得到3个候选；选本轮首个China Daily网页，Fetch在约0.006秒预检返回 `candidate_dns_refused:non_public`，未进入HTTP或费用预留，无正文。该分类来自应用，不是工具审批拒绝或供应商403。没有换目标／DNS／代理／机器重试。
- 累计实际端点请求2/6，均为成功Search，账本估算¥0.01/¥0.03、无未知工具费用；余4次／¥0.02仍属原批次。本轮已按预检拒绝停止，不自动用完。无新增模型调用。

第二次脱敏回执 `tmp/web-discovery/public-receipt-v2.json` SHA256 `de130512668ce1c07db18da3b716a723b52d91d6940cabc0f9ff87a37d679cd3`；完整账本与许可由ignored操作者记录持有。搜索标题／摘要不是当前事实确认，两次公开手工选择也不证明Jo真实模型的自主取舍。

历史两次DNS拒绝回执不变：14:47只留non_public大类，没有保存地址或更细类别，不能分辨RFC1918、环回、保留测试网段或fake-IP。14:00甚至没有底层分类。它们不证明网站不安全。public_dns仅留作原诊断复现入口，运行路径不调用；本轮没有新增解析、网络扫描、代理读取或配置修改。只读系统配置的PAC与nameserver观察不能判定根因。

## 可复验与仍欠

`python -m unittest discover -s tests -p test_web_lookup.py -v`、`test_public_dates.py`及 `make verify`覆盖合成演员／假HTTP的共享工具与记忆、当前卡片、改口／取消／旧结果、错误／截断、账本／计数／未知费用、URL／DNS拒绝。新增回归让演员看见合成私有包，经真实适配器和假HTTP走三轮Search→Fetch：私人生日、当前用户日期、恶意正文诱导旧日期；每次出站只有固定公开词或本轮URL，当前用户日期仅留本地。还验证构造后篡改日期在发送前拒绝、LA跨年日期关联，以及六次累计后第七次拒绝。模型输出仍是预写合成数据。

2026-10-31 mini旧reader的4次NHM真实读取已收束：取消／迟到隔离成立，改口丢原话被实读复现；共享生命周期已修并离线验证，未网络重跑。旧证据保留#11。

**历史检查点（2026-10-31，不是当前下一步或剩余额度）：** 独立审阅本架构修正后，再依据原许可／累计账本恢复有界API观察；本轮没有重试旧目标或新增工具HTTP。候选ID、URL词法、官方API与费用检查必须通过，供应商403立即停批，费用未知保留预留。现有4次／¥0.02不重置或自动变成新批次。模型回放可单独使用既有公开Search摘要和原DNS失败记录，但必须标明历史回放、无正文，不能冒充实时Fetch。当时尚缺真实Fetch正文／供应商安全观察、完整Jo行为、同料比较冻结、GPU／浏览器音频及Owner接受；后续证据及仍未验范围见 [trial](trial.md)。

本架构回归用假HTTP证明本机DNS无论返回保留地址或解析失败都不会被远端Fetch调用；实际请求只有官方余额和Fetch API。另证危险编码／私网字面量等在费用前拒绝、API302不跟随、供应商403关闭响应并跨适配器停止后续请求、取消继续保留未知费用。原直连DNS同组地址／混合私网拒绝／逐跳回归仍在完整门禁中。它们不构成供应商内部安全机制的实测证明。

PR25后续独立审阅补回两个反例：尾点在数值主机检查前规范化，标准／八进制／十六进制／混合形式及尾点均按词法拒绝；另用真实适配器＋假HTTP重现B等待余额期间A记录403，B恢复后不发POST、未发送预留以零结算。原cad96394保留审计，不把其测试通过当作上述漏洞当时不存在。

</details>
