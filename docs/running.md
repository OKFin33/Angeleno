# 独立开发与运行

当前累计候选、栈状态及启动／停止／恢复的单一交接见[试运行交接](trial.md)。本页保留通用运行说明。

本仓包含人物／记忆核心、DeepSeek provider 与费用保护、LiveKit 音频适配、Paraformer ASR、VoxCPM2 HTTP TTS、通话页和启动器。运行只依赖本仓、声明的公开软件包及操作者显式提供的资源；不需要另一个私有代码仓或旧 patched checkout。

这里说明启动路径，不宣称已经完成真实设备通话验收。实测范围见 [baseline](baseline.md)，产品能力与验收见 [Spec](spec.md)。

## 开发环境与离线检查

完整音频适配使用 **Python 3.12、macOS／Linux**；SQLite锁使用 `fcntl`，PCM重采样使用3.12的 `audioop`。原生 Windows Python 不在当前支持范围；Windows可在获准启动的Linux环境内运行。需要 Git、[uv](https://docs.astral.sh/uv/)；页面回归另需 Node.js。

```sh
git clone https://github.com/OKFin33/Angeleno.git
cd Angeleno
uv sync --locked --group dev
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -m her_agent.demo
.venv/bin/python -m her_agent preview --env-file .env.example
node tests/test_call_page.js web/index.html
```

测试使用合成人物、假HTTP／WebSocket和合成音频，不需要模型密钥、麦克风、GPU或设备启动。`preview`只编译指定人物；只有明确传 `--memory-user` 才读已有记忆。`demo`预编写整理结果，不是实际模型表现。

`examples/demo/` 是公开接线用合成人物“林”，与 Jo 产品人物不同。页面根据实际包显示示例或人物名，不能用该包的表现代替 Jo 验收。

## Interaction language (v0.1.1)

`ANGELENO_LANGUAGE=en` is the new default for Jo's spoken replies and the call page.
Set `ANGELENO_LANGUAGE=zh` in your own `private/runtime.env` to restore Chinese defaults.
Use one value consistently for the worker and page server. Invalid values fail the offline configuration check.
The setting does not change character files, source IDs, identity seeds, existing memories, models, reference audio or tool policy.
Chinese author material remains the source of the same character; it is not a requirement to recite or reply in Chinese.
An explicit user request to switch language is followed within the conversation; it does not rewrite the deployment default.

Paraformer still sends mono PCM16 at 16 kHz. Its explicit hints are `['en', 'zh']` in English mode and
`['zh', 'en']` in Chinese mode, retaining both languages. The SDK result language marks the configured primary
recognition language, not verified automatic language identification by the service.
English sentence segmentation handles periods while preserving decimal prices, abbreviations, initialisms and times such as
`USC`, `Los Angeles`, `7:30 p.m.` and `$12.50`; the text is not rewritten to guess pronunciation.
The same reference-only VoxCPM2 path is used. Cross-language voice quality and accent still need listening verification;
no reference transcript is fabricated, and the reference audio is not sent to cloud TTS.


## 配置、人物与私有状态

```sh
mkdir -p private
cp .env.example private/runtime.env
chmod 600 private/runtime.env
```

在私有文件中填写配置。环境变量优先于文件；可用 `--env-file PATH` 或 `ANGELENO_ENV_FILE` 明确选择其他文件。解析器不执行shell、不插值，也不发现宿主账号文件。

- `ANGELENO_AUTHOR_PACK` 指向本仓作者包，或操作者提供的独立资源包；manifest中的正文必须在包内。默认完整 Jo 包随仓位于 `characters/jo/author-pack.json`；`examples/demo/` 是独立合成接线示例。不要把新包指向旧seed的记忆库。
- `ANGELENO_DATA_DIR` 默认 `private/state`，需保持在本仓 `private/` 内。人物与用户身份下分别保存数据库，费用账本和ASR账本独立保存；原始聊天、输入快照和数据库不进入Git。
- `ANGELENO_MEMORY_USER` 明确选择身份。首次创建需 `--memory-create` 或临时设置 `ANGELENO_MEMORY_CREATE=1`；创建后恢复为0，恢复时遇到不存在的身份、seed／schema不兼容或占用直接失败，不自动清库或改身份。
- `ANGELENO_TIMEZONE` 默认 `America/Los_Angeles`，只影响运行日期；不改作者事实或seed。

文件存在不等于获得运行许可。按操作者有效授权和暂停状态执行；已有设备暂停须由明确恢复通知解除。

## 模型、费用与文字入口

配置 `DEEPSEEK_API_KEY`、模型、币种、输入／缓存／输出价格与来源、余额止损线及历史未知费用预留。价格从[官方页面](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)核对，按每百万token计；可以用已标明的保守上界做保护，但账本估算不能冒充钱包实扣。

每次模型请求前读取[官方账户余额接口](https://api-docs.deepseek.com/api/get-user-balance/)，缺配置、查不到余额或预留后不足即不发送请求。actor、工具续接和JSON维护共享入口；用量未知、失败或取消的预留保留。更换仓库、进程、账本或模型不产生新预算；历史回执与已授权金额由操作者接续，不能把示例配置当作授权。

在确认账户与运行授权后显式开启 `ANGELENO_PAID_ENABLED=1`。开启后的下列命令会收费：

```sh
# 首次创建独立身份；此后的重开去掉 --memory-create
.venv/bin/python -m her_agent chat --memory-create
# 已有身份的后续交流
.venv/bin/python -m her_agent chat
```

文字 CLI 的 `ANGELENO_MEMORY_TOOLS=1` 恢复每轮最多一次记忆查询、最多两次 actor 请求，只查询当前身份的记忆。语音同时启用地方核对时的共用限额见下节。普通聊天不强制调用工具。维护使用同一 provider 的 JSON 输出，失败保留原文和旧状态。

CLI保留 `/memory`、`/sync`、`/private`、`/public`、`/forget ID` 与 `/exit`。`/sync` 可能收费；`/private` 停止保存新交流。删除已有内容必须显式操作，单独备份不会随删除自动消失。

## 语音服务与通话页

实际语音需要：配置好的模型账户、Paraformer密钥、有限的 `ANGELENO_ASR_MAX_SECONDS`、LiveKit服务，以及已经就绪的VoxCPM2 HTTP服务。ASR按发送音频时长独立预留，跨通话和进程累积，不存原始音频。没有明确时长额度则拒绝开始。

先离线检查；该命令不创建记忆、不探测远端URL、不连接GPU：

```sh
.venv/bin/python scripts/run_voice.py check
```

首次运行尚无身份库时，给下列 check／start 命令临时加 `ANGELENO_MEMORY_CREATE=1`；
check 仍只读，真正启动才创建本机用户状态。后续启动去掉该前缀；不复制他人的身份库，也不以初始化重置已有记忆。

检查通过只证明本地配置可用。`ANGELENO_PAID_ENABLED=0` 仍是安全默认值，实际启动还需要明确开启。服务就绪后，选择一种启动方式：

```sh
# 使用已经配置的LiveKit服务
.venv/bin/python scripts/run_voice.py start
# 或由本次进程同时管理已安装的本地LiveKit executable
.venv/bin/python scripts/run_voice.py start --livekit-bin livekit-server
```

自启模式只接受loopback `ws://` 信令地址。默认网页 `http://127.0.0.1:8767`，信令7880、媒体TCP7881／UDP7882、worker8081。端口占用直接失败，不杀占用者；Ctrl-C只关闭本次创建的子进程组，不安装系统服务或修改操作系统启动项。

跨设备访问需操作者已有的HTTPS与媒体网络配置：设置 `LIVEKIT_PUBLIC_URL`、`LIVEKIT_NODE_IP` 和明确的网页Origin；非本机配置拒绝演示key和弱secret。浏览器麦克风需要可信来源。当前仓库不自动配置Tailnet、开放公网或启用远端设备。

页面显示配置状态，不把它当作 GPU／模型在线证明；拨号失败、识别／回复／播放错误和挂断均有停止路径。完整播放、打断、重拨和具体设备表现仍须真实验证。

稳定地方读取可显式 `ANGELENO_PLACE_TOOLS=1`（默认 `0`），不回退人工fixture。主动Search/Fetch另由 `ANGELENO_WEB_ENABLED=1` 及[Web发送策略与账户保护](web-lookup.md)启用，优先于稳定读取；两者复用同一语音核心、定向卡片与页面，不另开查询服务。完整私有试用使用[有界产品入口](trial.md#启动与停止)。每轮总计最多三次工具、四次模型请求；Search/Fetch可同类重复，其他工具每类一次。来源卡片不能代表完整语音交付，真实浏览器语音＋Web联合行为仍须验证。所有语音会话都需要本版token入口提供接收者metadata；旧版手工dispatch缺失身份时拒绝开始，不降级广播。

常规回答首音初始保护为15秒，查证回答为30秒，均从确认输入的同一单调时钟计算。短提示、查询完成或卡片到达均不重置期限。超时停止该轮，页面提示继续说话；模型、查询、TTS取消或媒体清理失败才停止通话。这里只在服务端观察播放开始，用户实际听见及首句是否为有效结论须按 [voice lane](voice-lane.md) 测量，不能以保护未触发宣称体验通过。`session.userdata['angeleno_timing']` 保留最近100轮的内存时间标记及锚点，不含原话，不自动写日志；程序输入的 generation_start 回退不能冒充 ASR 确认时间。

首音出现后，如果模型生成尚未结束（包括提示音之后工具参数仍在流式生成），仍保留从原确认时刻起30秒的外层保护。该保护不猜对白语义，避免完整工具事件尚未到达时无限等待；已经完成生成的正常回复仍按实际播放结算。

## GPU与声音资源

GPU服务是独立可选环境，不由默认 `uv sync` 安装torch或下载权重。需要支持CUDA的Linux／NVIDIA环境及匹配驱动。`gpu/requirements.txt` 固定既有后端的关键版本，其余依赖仍需记录目标环境解析结果；这不是已验证的全平台锁文件。

```sh
uv venv --python 3.12 gpu/.venv
# 在获准的GPU环境，先从PyTorch官方CUDA 12.8源安装匹配torch／torchaudio
uv pip install --python gpu/.venv/bin/python --index-url https://download.pytorch.org/whl/cu128 torch==2.7.0 torchaudio==2.7.0
uv pip install --python gpu/.venv/bin/python -r gpu/requirements.txt
```

操作者单独提供已下载的 `openbmb/VoxCPM2` 本地模型目录，并记录模型revision和校验；核对模型来源与许可。通过 `VOXCPM_MODEL` 显式指定目录，通过 `VOXCPM_REFERENCE` 指定有权用于测试的本地参考声音。模型和声音都不随Git分发；服务不会自动选择旧文件或下载缺失模型。

随仓默认人物包是 `characters/jo/author-pack.json`；公开示例 `examples/demo/` 仅用于接线测试。
参考音频不随人物包分发。将自己有权使用的参考放在 **GPU 主机**的 `private/voices/reference.wav`，
或者在 `VOXCPM_REFERENCE` 配置其他明确路径。worker 只核对逻辑资产和摘要，不要求复制声音到另一台机器。
当前服务走 VoxCPM2 的 **reference-only** 路径（`reference_wav_path`），不需要参考音频转写；
没有启用 continuation／combined 的 `prompt_wav_path` 和 `prompt_text`，也没有视频上传或自动提取音轨入口。
建议使用清晰、单一说话人、无背景音乐的 PCM16 WAV；现有真实参考的已验证输入是44.1kHz双声道PCM16 WAV，
内部解码／声道及采样转换由固定 VoxCPM2 loader 处理。其他容器和编码不作为本版验证承诺；不要把视频改后缀当音频。
不同声音和录音条件仍需自己验证质量，文件摘要不证明说话人或声音许可。

在 GPU 主机计算所选文件摘要，将结果填写到 worker 与 GPU 使用的私有配置：

```sh
python3 -c 'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("private/voices/reference.wav").read_bytes()).hexdigest())'
```

使用 `.env.example` 已列的 `VOXCPM_REFERENCE_PURPOSE=jo`、任意合规逻辑 ID（默认 `local-reference`）和真实 SHA-256；
`jo` 是人物用途标记，不表示仓库自带某位说话人的声音。缺文件或缺摘要时停止，绝不回退旧音色。

正式Jo运行还必须显式设置 `VOXCPM_REFERENCE_PURPOSE=jo`、逻辑资产ID `VOXCPM_REFERENCE_ID` 及所选文件的 `VOXCPM_REFERENCE_SHA256`。GPU在导入／加载模型前验证该文件可读且摘要匹配；worker本地配置检查要求相同声明，并在加入用户房间、构造ASR前核TTS health的用途、资产ID和已验证摘要一致。缺配置、不可读、摘要不符或旧health缺少证明均停止启动，不选择旧默认文件。摘要只证明操作者选定的文件，并不识别说话人；`voice=jo` 或文件名本身都不是声线证明。更换文件需重新验证并重启，不在运行中替换。

仅内部TTS／PCM技术诊断可显式使用 `VOXCPM_REFERENCE_PURPOSE=diagnostic` 与指定参考路径；health和PCM响应头会标明diagnostic，该模式不能通过Jo worker启动校验。原技术启动器若仍从旧配置继承参考路径，必须先改成明确用途和所选资产声明后才能使用新GPU代码。此处不提供、复制或自动生成任何真实音色文件；尚未获准访问所选资产时保留缺项。

```sh
# 填好本机资源路径后，显式加载私有配置启动GPU服务
uv run --no-project --python gpu/.venv/bin/python --env-file private/runtime.env python -m gpu.server
```

默认监听loopback18080，`/health`只在模型加载成功后就绪。配置 `VOXCPM_URL` 指向该服务。启动器不自动恢复已暂停设备，假模型协议测试也不证明CUDA和真实音色可用。

句流结束／取消会显式关闭正在使用的TTS流，不依赖垃圾回收。可在worker启动配置中设置 `ANGELENO_TTS_PREFETCH_FRAMES=2`，用有界顺序预取覆盖下游消费停顿时的部分后句准备；默认 `0` 保持串行。仅允许0或2，错误配置在启动资源前报错。启用时最多缓存2个SDK音频帧及1个等待入队帧，单GPU合成仍串行，取消会丢弃旧轮待播帧并关闭句流；SDK内部缓冲不在这个容量承诺内。不新增起播等待、不改变15／30秒保护，也不能弥补长期RTF≥1的生成不足。现有真实AB有顺序与随机性混杂，参数可运行不等于浏览器连续性已改善；下一次实际对照需明确开启该参数。

显式 `VOXCPM_COMPILE=1` 选择官方编译路径，默认 `0`；GPU 与 worker 的该配置应一致。
编译需要 GPU 主机已有可用 C/C++ 编译器和匹配 Python 开发头文件；Ubuntu 24.04／Python3.12 的已验证组合为 `gcc`、`g++`、`python3.12-dev`。
这些是可选优化依赖，不由应用启动时自动安装；缺失则先使用默认 eager，不改驱动或降低生成质量。`/health` 的优化活动标记只有模型完成预热后才有效；
worker要求它与本次候选匹配。私有资源启动器和 `scripts/run_trial.py` 使用同一 `--tts-candidate compiled`，
独立预热成功后才开放用户加入；失败可回到默认 `eager`，不改变用户身份、音色或账本。

GPU 启动时对已验证参考音频编码一次，随后按同一已加载模型和参考内容摘要复用 VoxCPM2 的 reference 特征；每句仍创建独立生成状态，保留原10步、CFG与取消释放。缓存只在本进程内存中，不保存或上传；运行期间参考路径或内容改变会拒绝合成，须重新验证并重启。`VOXCPM_REFERENCE_CACHE=0` 可回退原逐句编码路径作诊断，默认 `1`。此项不自动打开 `VOXCPM_COMPILE`，编译仍按原配置。

音频性能观测默认关闭。仅在有界诊断中给GPU与worker进程显式设置 `ANGELENO_AUDIO_METRICS=1`；worker另需既有 `ANGELENO_DIAGNOSTICS_FILE` 指向本仓ignored `private/` 内0600文件。GPU数值计量、worker新增块计量各自每进程最多2048条，跨句／请求共享，不随请求重置；到限只停止计量，服务和原生命周期事件继续。GPU输出仅数值JSON到当前私有进程日志；HTTP相关ID是每句新生成的随机值，不含对白或参考声音标识。记录生成／收块时序、PCM累计秒数、RTF（墙钟秒／音频秒）与本地队列；GPU推理RTF扣除输出队列等待，EOF排空不增加推理时长。worker同步push耗时不等于实际播放队列，SDK未公开的队列时长记为未知。预缓冲可跨句累积，不能把对应推帧精确归因到单句。

页面仅显式访问 `?audio_metrics=1` 时按秒读取音轨公开RTC统计，保存在本页 `window.angelenoAudioMetrics`，最多120条、120秒后停止，不上传。记录丢包、jitter、concealed samples和累计jitter-buffer平均等待；后者不是当前队列深度。不保存对白、音频、地址或用户／房间标识；缺失计数保持未知，挂断停止采样并拒收迟到结果。下一次诊断最多120秒，并仍受原累计GPU、ASR和请求时限约束；本观测开关不会启动服务或扩大许可。

第三方代码和浏览器包来源见 [NOTICE](../NOTICE)。原创代码与文档使用 [MIT](../LICENSE)，NOTICE 列出的改编文件保留 Apache-2.0；Jo 作者材料的独立许可见 `characters/jo/NOTICE`。参考声音、模型权重与生成语音不由这些代码许可证授权。

可选归因保护：`ANGELENO_MEMORY_ATTRIBUTION_CHECK=1` 可用于文字或语音私下试用，默认 `0`。它在提交新增用户事实／偏好／拒绝及相关episode前，用同一维护模型增加至多一次紧凑检查；明确纠正优先，不把角色说法当用户认可。针对已复现的单数指代→量化拒绝范围扩大，另有窄保守门，即使模型误判supported也保留未明，不自动扩大范围。原始交流和旧确认记忆保留；超时、取消、无效或不支持的判断不提交、不自动重试。两次维护调用共享既有超时预算，原5秒关闭上限不变；不阻塞前台语音，但会增加维护费用。有限中文范围门和几个通过反例不等于通用语义可靠，默认不改为开启，正式比较的默认配置不变。这不是USER.md或用户熟悉度设计。


### 真实试用与开发资料分开

真实语音入口显式使用 `ANGELENO_MEMORY_PURPOSE=real_user`。技术语音入口使用 `development`、`dev-` 开头的独立身份及 `ANGELENO_MEMORY_DATA_DIR=private/development-state`；比较器也显式标记开发存储。内存用途绑定后不可在两者之间悄悄切换，未标用途的调用不能打开已绑定的库。`ANGELENO_MEMORY_DATA_DIR` 仅分开人物／用户记忆，不改变原 `ANGELENO_DATA_DIR` 中的费用或 ASR 累计账本。

已绑定或隔离的库使用 memory schema3，旧 schema2 写入器必须拒绝，恢复时使用包含本修复的开发版本。明确有回执的开发记录可在一致备份后隔离：原文保留，原交付状态和依据另外保留，纯派生记忆停止供给；混合来源摘要不能自动整条当作合成丢弃。身份、seed 和真实历史不重置。原来真实说过的助手推断仍是助手发言，不能据此认证用户事实。

新通话尚未提出当前话题、输入仅为寒暄或附和时，不凭历史记录续办旧工具任务；明确续接、同通话内确认以及当前实际安排仍可正常使用工具。这个窄的寒暄识别不是通用意图分类器，也不是禁止闲聊中合理查证。

`USER.md` 暂为待实施设计：如采用，它是用户／操作者明确维护的资料权威，模型摘要仅派生并引用该来源，不能自动回写；与旧推断冲突时保留来源和纠正，不再复制成第二套权威用户事实。暂未创建该文件、未加入熟悉状态机，既定朋友起点仍沿 Spec。


### Development tool decisions

For synthetic development sessions only, set `ANGELENO_TOOL_TRACE=1`,
`ANGELENO_MEMORY_PURPOSE=development`, and the existing `ANGELENO_DIAGNOSTICS_FILE` to a new private 0600 receipt.
The trace is off by default and records at most256 events per process: proposed/validated calls, batch rejection,
local adapter start/end, and skipped calls with a controlled reason. Only tool names, hashed call IDs/arguments,
turn numbers and controlled statuses are retained; no raw parameters, responses, user speech, prompts or hidden reasoning.
`tool_adapter_start` is not evidence of an HTTP request or successful lookup. Existing Fetch HTTP events and the account
ledger provide separate network/billing evidence. Do not use missing old trace rows to invent a historical decision.
