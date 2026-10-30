# 地方条目、来源与结果事件

本文冻结离线契约。行为依据是 [Spec](spec.md) 第 3、4 节。这里不接入人物运行、语音或页面，也不发起网络读取。

加载 `her_agent.places` 只读本仓 `data/places/`。当前核对必须传入 `FixedClock`。包内没有墙钟、套接字或模型调用。

## 数据

| 路径 | 内容 |
| --- | --- |
| `data/places/entries/*.md` | 稳定条目。易变事实不写进摘要 |
| `data/places/aliases.json` | 与条目别称逐条一致的别称表 |
| `data/places/sources.json` | 来源登记。`last_success` 在生产读取成功前为空 |
| `data/places/fixtures/` | 冻结页面、私网陷阱页和 `manifest.json` |

条目至少有稳定 ID、名称、别称、区域、主题、摘要、来源 URL 字段、核对日期和是否易变。来源 URL 可以是空列表：2026-10-30 的调查没有留下确切 URL 时，不补写主机。已启用的来源，条目 URL 与允许 URL 一致。

核对日期 `2026-10-30` 表示条目与该日调查对齐。它不是生产抓取时间，也不能填进 `last_success`。

夹具里的开放时间、例外和冲突是给 `places.extract.v1` 的契约样本，不是线上页面的字节副本。样本里的句子只在通过提取规则后成为证据。

覆盖：

- 核心生活圈：Jefferson Park、USC、Exposition Park
- 可查证文化场所：USC Fisher Museum of Art、Natural History Museum of Los Angeles County
- 已知但禁用：CAAM 访问页无可用正文；Exposition Park 官网 403；City Planning 的 Jefferson Park 页超时
- 次要节点：Culver City、Sherman Oaks。只保留定位，不查当前安排

人物虚构地点不进入本包。

## 检索

`Catalog.search(question, place_id=None, area=None)` 做名称、别称和摘要检索。

- 无命中是 `coverage_gap`。结果里没有“不存在”字段
- 多个互不包含的别称命中时 `needs_clarification`，不挑选一个
- 更长的别称包含更短的命中时，保留更长的那条
- 显式 `place_id` 只返回该条；未知 ID 抛出 `KeyError`

## 时间

默认时区是 `America/Los_Angeles`，用时区库处理夏令时。`resolve_when` 的规则：

- `今天`／`明天`／`昨天` 取注入时钟的洛杉矶民事日期
- 单独的周几是含今天在内的下一次；`这周` 是含今天的周一至周日；`下周` 是该日再加七天
- 末尾的 `上午`／`下午`／`中午`／`晚上`／`白天` 只记成 `time_hint`，不换成钟点，也不推翻页面证据
- 用户另外声明时区时，相对日期若会落到另一个民事日，返回澄清，不猜测
- 写明的 `YYYY-MM-DD` 按该日历日保留
- 无法解析的说法，包括单独的“周末”，返回澄清

读取时间、页面更新时间和事实适用日期分属 `checked_at`、`page_updated_at`、`applicable_date`。缺的保持空。

## 来源与 URL

启用的来源必须有冻结夹具、一至两个 `https` 允许 URL，以及解析器 `places.extract.v1`。禁用原因只允许 `timeout`、`http_403`、`no_usable_body`、`not_surveyed`。禁用项没有夹具，检查时不打开页面。

`vet_url` 只接受与某条允许 URL 在方案、主机、路径上完全相同的公开 `http` 或 `https`。拒绝：

- 字面私网、环回、链路本地、非全局地址、十进制或简写数字主机
- `localhost`、`.localhost`、`.local`、`.internal`
- 非 HTTP(S)、用户信息、查询串、片段、`..`、非默认端口
- 未登记主机或路径，包括从 `https` 降到 `http`

嵌入的 meta refresh 若指向拒绝目标，只记入 `refused_navigations`，不打开其他文件。跳转链在打开夹具前检查；链上任一跳被拒绝时不读取正文。一次检查最多两个目标。外部读取期限是 10 秒，`external_read_exhausted` 从第 10 秒起为真。离线夹具不消耗这个期限；以后的生产读取器要调用它，不能把本模块的本地读当成线上时限已验证。

本模块不解析 DNS。公共主机名若实际指向私网，当前契约发现不了。

页面和条目里的指令只是数据。脚本、表单和 `data-instruction` 块不执行，也不能改登记。

## 提取与状态

提取器只认 `article[data-claim]`。自由散文不能变成有依据的结论。

同等主张的优先级：所问日期上的 `exception` 高于 `standing`；`undated` 和 `social` 都不能覆盖更高的明确主张。同一级文字不一致时状态为 `conflict`，`conclusion` 为空，不取第一条。提取器不改写句子，也不把“周三至周六”展开成每一天。

| 状态 | 含义 |
| --- | --- |
| `supported` | 有一条胜出的证据，引用里有摘录 |
| `insufficient` | 没有可用主张、只有无日期文字、缺适用日期，或正文被截断 |
| `conflict` | 同一级至少两条不同文字 |
| `unavailable` | 来源禁用，或导航被拒绝 |
| `out_of_scope` | 没有来源，或问题类型不在该来源的可回答范围内 |
| `cancelled` | 请求已取消。结论为空 |

截断时状态是 `insufficient`。截断前已经完整出现的例外仍留在引用里，但不能升成有依据的结论。

## 事件与取消

`ResultChannel` 把查询绑到会话、用户轮次、请求 ID 和谱系。`open` 发出核对中的卡片，结论为空。补充条件走 `revise`：原话不变，旧请求取消，新版本使用新的问题和适用日期。新的 `open` 替换该会话的当前卡片，并取消旧谱系。

`publish` 接受仍有效的结果事件，并更新这一张卡片。下列情况拒绝，且不把迟到结论写进卡片或语音：

- 请求或谱系已取消
- 版本、轮次、会话或当前卡片对不上
- `supported` 没有带摘录的引用
- `conflict` 没有至少两条冲突文字
- 事件通道不是 `source_status`，或 `speech_text()` 不是 `None`

事件字典没有 `tts` 或 `speech`。`append_speech` 拒绝夹入通道名、请求 ID 或整段事件 JSON。普通对白可以包含已经核实的事实措辞，那不是事件本身。

取消后的当前卡片状态是 `cancelled`，结论为空。已经展示过的结果在换题后不再改写成新题目的结论。

## 复用

同一次检查器里，同一会话、地点、来源、问题类型和适用日期可以复用已解析的证据。`checked_at` 保持第一次读取的时钟值，`detail` 为 `reused`。换会话、换日期、`recheck=True` 或正文被截断时重新读夹具。复用不跨检查器，也不写回来源文件。

## 复核

生产抓取器要另作验证。启用来源在真实读取成功之前，`last_success` 保持为空，夹具也不能换成未记录的线上正文。Spec 没有记下确切 URL 的禁用项，要由维护者补 URL；补上以后仍保持禁用，直到该页单独读到正文、日期或错误状态。
