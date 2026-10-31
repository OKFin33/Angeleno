# 三段开发比较的离线运行入口

沿 [Spec §8](spec.md#8-评测接受与交付) 和 [Issue12](https://github.com/OKFin33/Angeleno/issues/12)。
运行三段完整开发输入、两种角色和共用生产核心，检查执行条件；目前**只提供 dry-run**。
脚本演员与维护输出预先编写，不是模型生成、Jo表现、Owner校准或比较结果，没有付费开关。

```sh
.venv/bin/python scripts/run_development.py --output private/evaluation/development-dry-run.json
```

输出必须是本checkout的 `private/` 或 `tmp/` 内新文件；已有回执不覆盖。
默认读取公开 `examples/demo` 合成人物，按 `assistant_control_pack` 构造普通助手。
不加载runtime.env、密钥、实际Jo稿、旧聊天、生产记忆或保留集。
原Jo与六组共同事实补充的本地检查由ignored私有入口接到同一个runner，不复制原文进Git。

## 固定的共同条件

唯一配置 `examples/comparison/development.json` 绑定三段输入、语料hash、固定LA时钟、模型别名、
actor／writer温度和输出上限、每轮4次模型／3次工具、每段及整批请求硬计数、超时和两臂次序。
两个角色共用 `ConversationRuntime`、工具协议、全部grounding、检索集合及同一记忆机制；
每臂每段新建独立临时库，运行结束删除自身创建的库，不恢复旧状态。
演员日期、工具时钟、记忆发言时间一致。时钟与网络禁止补丁作用于本进程，必须串行运行。

工具续接、失败和取消都先计一次尝试，不因异常退还计数、不换角色重置整批计数。
该离线计数不代替未来真实运行的原账户费用账本、余额止损与未知用量预留。
目前160次actor／18次writer是三段全部已列分支的保守护栏提案，不是预期消耗或批准的新批次。

文本逐片进入回执缓冲区，只有完整生成才按完整文字交付结算；失败保留已收到前缀并标中断。
每第三个完整回答沿原runtime安排维护，runner在下一轮之前等待该任务结算。
段末再flush剩余批次；维护失败／超时／未结算即停止该段，关闭时不再重试。
不把后台任务还在跑时的下一轮请求当作公平的同状态比较。
保存完整输入／脚本输出、卡片、查询、请求状态、材料hash和失败；不产生偏好分或赢家。

## 检索和澄清

`corpus.synthetic.json` 显式列出全部24种合法公开查询组合。
相同出站词必须获得相同记录，不能因内部area不同给同词不同资料。
其中7种有合成匹配、17种明确为空；“空”仅说明这个合成集合没有匹配，不能说明现实不存在。
遗漏映射是配置失败，不自动联网补齐。两臂共享normal／empty／conflicting_body／truncated_body case。
这补齐了机制覆盖，**没有把小型合成包升级为正式人物偏好语料或真实网页快照**。

`anchors.json` 复用既有D1—D3和已列澄清，结果字段全部为空。
离线脚本不提出澄清；分支接口可接已冻结的选择器／操作员，返回列出的分支ID且仅可使用一次。
未列问题、重复分支、尚无冻结解析器的动态指代回答均停止为 `branch_uncovered`；
不靠关键词猜真实模型问了什么，不让代理临场优化一臂。真实比较前必须落实这一分支接回方式。

## 验证与尚欠

`tests/test_comparison.py` 覆盖六段完整流程、两臂同工具／时钟／材料入口、24种查询与四类语料、
澄清与未知分支、失败／取消计数、跨臂总上限、维护失败不重试、维护屏障、误联网阻止、文件漂移和付费开关拒绝。
`make verify` 自动包含这些测试。真实配置加载不在脚本中，网络连接／DNS／配置加载另被离线保护拒绝。

真实三对输出仍须固定候选、实际模型版本、合适的共同检索材料、角色事实同料检查和完整批次分配，
并在原许可／原账本下通过另经审阅的真实运行入口执行。没有靠改一个参数开启真实服务的路径。
然后才请Owner校准三对；校准不是生成开发输出的前提。
4—6条保留情景仍由独立准备者在后续阶段落实，本入口不读取或生成其正文。
真实Fetch正文、Jo自主工具行为、真实媒体和首版接受分别记证据，不用此脚本成绩替代。

## 显式真实模型入口（尚未执行）

```sh
# 默认仍是完整离线公开合成演练；不加载凭据或Jo
.venv/bin/python -m her_agent.comparison_live --output private/evaluation/<新回执>.json

# 只在具体载荷许可／审批阻碍已解决、材料已冻结后，由操作者显式执行
.venv/bin/python -m her_agent.comparison_live --run \
  --config private/evaluation/<共同配置>.json \
  --manifest private/evaluation/<冻结清单>.json \
  --env-file /<原运行目录>/private/runtime.env \
  --output private/evaluation/<新真实回执>.json
```

本候选只实现和用假HTTP验证入口，没有执行上述`--run`或再次发送被拒Jo载荷。
`examples/comparison/model-manifest.template.json`故意未冻结、字段留空，不能直接运行。
清单不是授权；先前私稿外发的审批拒绝必须沿原动作解决，不能换这个入口试发。

清单须绑定当前干净commit、共同配置文件hash、已存在原运行root／原账本绝对路径、
原人物manifest及两臂完整provenance指纹；共同补充以`shared_grounding`的path／sha256逐项加载，
在内存附到人物pack后交同一助手工厂，原稿不改。配置仍由原`load_config`核三段输入／资料hash、
同模型／温度／上限／时钟／顺序，不另复制一套实验参数。`status`须为`frozen_development_inputs`，
`authorization_reference`记录已有具体许可；`corpus_acceptance`写清选定资料能证明什么，
`model_version_limitation`如实记录别名与不可变版本证据的区别。不自动生成冻结或评分。

真实模式从显式原runtime.env消费配置，进程内因`--run`启用paid，文件不修改。
每段构造原`configured_providers`，actor／writer共用原预算对象；实际model／输出上限须匹配共同配置。
原账本必须已存在且路径一致，不另建账本。余额、未知预留、维护及续接沿原入口逐次记账；
任一次用量未知或已有未结算预留立即停整批，不充值、不自动重试。模型测试人工批次限制的解除
不移除每轮4模型／3工具／超时保护。所有模型请求的输入hash、输出前缀／tool_calls、维护JSON输出及账本快照写入新的私有回执。
每次更新用独占临时文件、fsync及替换保留最近现场；已有回执不覆盖。异常／取消保留已收到前缀，
先结束当段、关闭其provider／状态，再停止整批；后台维护在下一轮前等待、段末flush。
回执可恢复证据和未做部分，**没有自动续跑HTTP**；未明确结果的请求先查原账本，不盲重放。

真实模式仍只用双方相同的本地冻结`CorpusWeb`，不接在线Search／Fetch，不消费工具批次。
24组合须显式映射；未收集的组合可写`"record_ids": [], "uncovered": true`，执行即停，
不能用空数组冒充现实无结果。同一出站query词即使内部ID不同也须有相同记录／未覆盖状态。
语料可以保留合成控制或历史来源，但要明确接受范围，合成输出不能充当真实偏好证据。

分支策略固定为`stop_unclassified`。现CLI没有猜测模型问题的分类器／评分模型：
每次完整回答之后若没有经审阅的操作员分支适配，停止为`branch_uncovered`并留下输出；
不会把模型澄清当作普通回答直接跳到下一题。底层复用原`branch_selector`接点，后续只可按双方同一
既定程序返回一次列出的ID／继续／未知，动态指代还未落实时仍停止。本入口因此已经能接真实provider／
账本和形成可恢复输出，但在分支接回、资料冻结和私稿审批解决之前不能宣称三对比较就绪。
Owner三对校准在实际输出之后；保留题、赢家和Owner判断不由这个入口生成。
