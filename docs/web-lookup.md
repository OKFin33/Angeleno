# 主动发现与网页阅读候选

2026-10-31 Owner 明确选择主动发现新资料，替代仅预登记来源。人物性格／成长经历影响搜索和取舍；有效产品约定见 [Intent](intent.md) 与 [Spec](spec.md)，实施与真实验证回 [#11](https://github.com/OKFin33/Angeleno/issues/11)。本候选基于 UI #21 `7cdb9df`，不修改该固定 PR。旧 reader 保留回退和历史证据，不继续扩站点提取器。

## 同一条运行路径

`ConversationRuntime → lookup.reply → WebLookup → DeepSeekWeb → Search / Fetch → ResultChannel → VoiceCards / 页面`。

演员可直接闲聊、查询原有记忆或稳定地方条目，也可选择公开地区／主题／地点ID及必要日期，由程序构造查询，随后选择当前轮搜索返回的一个 `result_id` 阅读。没有强制每轮联网或预先问完偏好的脚本。统一总数仍是每轮最多三次工具、每类一次、四次模型请求；Search最多五条，Fetch一页，外部等待合计10秒，无重试。两种网络动作接既有15／30秒保护；这不证明真实语音延迟已通过。

**审阅后的临时发送边界**：旧候选 `35441a9` 只限制query长度，提示词不能防止模型把私人内容放进查询。当前API不接受原始字符串或调用者自称公开的对象；只接收 `PublicQuery`，其文字全部由源码内公开枚举和解析后的日期组成。枚举覆盖Jefferson Park／USC–Exposition Park、少量展览／建筑／公共空间主题及Fisher／NHM。未知ID、附加自由query等参数在发送和费用预留前拒绝，不用隐私关键词黑名单作保证。

真实运行入口还要求人物包完整等于仓库 `examples/demo/author-pack.json` 的合成公开材料；仅复制其ID不够。默认关闭不变，真实Jo或其他私有人物包即使手动启用也会拒绝。该措施为当前公开测试建立发送边界，**不等于已实现完整Jo自主发现，不修改已批准的产品范围**。基于私有人物选择公开查询所隐含的兴趣信号、任意新主题／地点的安全构造、真实模型是否遵守外部内容边界仍待解决；本候选不声称消除了所有语义信息泄露。Jo稿送对话模型的许可不用于开启Search。

卡片新增 `evidence`（已取得资料）状态，conclusion为空；搜索摘要与正文不是已核实事实。正文超4000字符返回截断／信息不足，保留未知，不从截掉的例外推断开放。HTTP响应限128KiB。发布日期、读取时刻和问题适用日期分开；字段缺失不猜。来源文字只作为tool数据，演员收到明确的非指令约束；模型实际遵循程度仍须真实情景验证。

显式取消结束查询关系；读取被插话中断只取消旧请求，后继 `supplement` 保留原始话语并增加版本。旧请求仍不可发布、播报或写成已完成交流。已读证据只有该轮实际完整交付时才沿原记忆入口留存；临时“不保存”模式仍不落盘。

## 运行开关与计费

既有模型配置和统一账本继续原地使用。默认 `ANGELENO_WEB_ENABLED=0`，无新网络动作。实际端点获准、并选用上述精确公开测试包后，操作者才能在该进程显式设 `ANGELENO_WEB_ENABLED=1` 和 `ANGELENO_WEB_REQUEST_LIMIT`（首批1–6）；CLI仍使用已有 `ANGELENO_LOOKUP_LIVE=1` 及 `python -m her_agent.lookup --live --utterance ...`，语音通过原运行入口。Web开关优先于原 `ANGELENO_PLACE_TOOLS`，不会并行建立两套查询服务。该开关或文档不是调用授权。

[官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Search schema](https://api-docs.deepseek.com/zh-cn/api/search/)、[Fetch schema](https://api-docs.deepseek.com/zh-cn/api/fetch/)于2026-10-31核对：固定官方 `/v1/tools/search` 与 `/v1/tools/fetch`，有结果请求各CNY0.005，空结果／失败不收费。演员和新工具共用同一个Ledger及每请求余额检查；模型仍按token计费。工具取消、无有效响应或HTTP200无法解析时保留全额预留，不能按失败猜成免费。工具计数跨新adapter／进程按同一账本累计，达到批次上限停止，不新建账本重置。

Owner随后批准仅公开测试内容、Search／Fetch合计至多6次、工具费至多¥0.03。2026-10-31T14:00Z在旧候选上执行1次人工公开查询，Search HTTP200、5条结果、约0.502秒，估算¥0.005；Fetch在本地DNS预检被拒，未发送／未预留。原批次剩余5次／¥0.025不重置，本次审阅修复没有新增调用。完整回执和实际操作许可在#11与操作者记录；不据此声称真实Jo模型选择或Search→Fetch正文成功。

## 网络与数据边界

API请求只有固定官方目标，禁止重定向、不用环境代理；密钥不传给结果网站。Search只发程序构造的query/count/need_content=false，不附ori_question、定位IP、作者稿、历史或原始用户话语。支持的公开构造及私有人物阻断见上文；这不授权任何新的真实调用。

Fetch只接受本轮已观察候选ID；当前保留原URL词法保护，只接受公开HTTPS、不含query/fragment/userinfo、非特殊端口的URL。未预登记网站可进入，私网／本机地址不行；在付费Fetch前检查本地解析地址，非公网或未知即拒绝，不为笔记本的DNS失败改安全门。没有对目标网站发送带密钥的请求。

**代理读取的实际限制**：本地DNS检查不是DeepSeek实际使用的解析，无法钉住其地址；Fetch返回仅title/content，不披露最终URL、跳转链、抓取时间或逐跳安全观察。官方提到安全拒绝403，但未给本项目可复验的逐跳明细。模型tool payload保留provenance；页面展开来源时，搜索摘要显示“尚未读取正文”，已取正文显示“由搜索服务读取；最终地址和跳转过程未提供”。不声称原直连读取器的逐跳证据已覆盖它。真实服务预跑与整体Review必须核该边界；本轮没有绕过任何私网或DNS拒绝。

14:00回执中的 `candidate_dns_refused` 由 `DeepSeekWeb.fetch` 在 `_request` 前归一化本地 `OSError`／`ValueError` 产生，是应用安全预检拒绝，不是工具审批拒绝或供应商403。该回执未记录底层异常／地址，不能区分解析失败、空答案或非公网；先前机器其他域名的dns_non_public不能替代这个目标的证据。只读系统配置看到PAC启用与公共DNS配置，不能据此确定原因或证明供应商解析安全；没有读取PAC内容、改变配置、重查该域名或重试Fetch。

## 可复验与仍欠

`python -m unittest discover -s tests -p test_web_lookup.py -v`：合成演员＋假HTTP验证新路径、现SDK合成播放和定向卡片、仅完整交付落库／不保存、日期改口原话关系、旧结果拒收、截断和错误、共享账本／计数／未知费用、危险URL／DNS拒绝；追加包含合成姓名／电话／地址的原始查询、伪公开标签、ID注入和同ID私有包反例，断言没有传输与费用记录。页面回归验证两种来源说明实际渲染。`make verify`另覆盖原全部门禁。它们不是实际模型选择、真实搜索资料、GPU音频或Owner接受。

2026-10-31 mini旧reader的4次实际NHM读取验证已收束：读取中取消与迟到隔离成立，改口丢原话关系被实读复现；本候选修该共享生命周期反例并离线验证，尚未重新做网络修后复验。原证据及SHA保留#11。

仍需修后独立Review、满足隐私边界的真实Jo发现能力、获准Search→Fetch正文实测及代理安全边界核验、同料比较条件冻结、真实GPU／浏览器语音和Owner判断。不能用脚本中的Jo台词或“已取得资料”卡片代替这些。
