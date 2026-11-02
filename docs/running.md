# 安装与运行

本页介绍开发环境、服务配置、自备参考声音和语音启动步骤。版本变化与验证范围见[版本说明](trial.md)，代码分层见 [baseline](baseline.md)。

完整部署由人物／记忆核心、DeepSeek、Paraformer ASR、LiveKit、VoxCPM2 GPU 服务和通话页组成。请先完成离线检查，再配置自己的服务账户与 GPU 资源。

## 开发环境与离线检查

完整音频适配使用 **Python 3.12、macOS／Linux**；SQLite锁使用 `fcntl`，PCM重采样使用3.12的 `audioop`。原生 Windows Python 不在当前支持范围；Windows 使用 WSL／Linux 环境。需要 Git、[uv](https://docs.astral.sh/uv/)；页面回归另需 Node.js。

```sh
git clone https://github.com/OKFin33/Angeleno.git
cd Angeleno
uv sync --locked --group dev
. .venv/bin/activate
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -m angeleno.demo
.venv/bin/python -m angeleno preview --env-file .env.example
node tests/test_call_page.js web/index.html
```

测试与 `demo` 使用合成人物、假 HTTP／WebSocket、合成音频和固定整理结果，无需服务密钥、麦克风或 GPU。`preview` 编译指定人物；传入 `--memory-user` 时读取该身份的已有记忆。

默认 Jo 作者包位于 `characters/jo/`；`examples/demo/` 是独立的合成接线示例“林”。页面按配置显示人物名。

## Interaction language (v0.1.1)

`ANGELENO_LANGUAGE=en` is the new default for Jo's spoken replies and the call page.
Set `ANGELENO_LANGUAGE=zh` in your own `private/runtime.env` to restore Chinese defaults.
Use one value consistently for the worker and page server. Invalid values fail the offline configuration check.
The setting does not change character files, source IDs, identity seeds, existing memories, models, reference audio or tool policy.
Chinese author material remains the source of the same character; it is not a requirement to recite or reply in Chinese.
Explicit direct requests such as `Let’s speak Chinese` or `我们换成中文聊吧` update the current call’s language state, even after the original request leaves the recent-history window.
Mentions, quoted examples and tool text do not change that state. Other wording still reaches the model but is not guaranteed to become program-held state.
A new call starts with the configured language; switching does not rewrite identity, saved history or the deployment default.

Paraformer still sends mono PCM16 at 16 kHz. Its explicit hints are `['en', 'zh']` in English mode and
`['zh', 'en']` in Chinese mode, retaining both languages. The SDK result language marks the configured primary
recognition language, not verified automatic language identification by the service.
English sentence segmentation handles periods while preserving decimal prices, abbreviations, initialisms and times such as
`USC`, `Los Angeles`, `7:30 p.m.` and `$12.50`; the text is not rewritten to guess pronunciation.
The same reference-only VoxCPM2 path is used. Cross-language voice quality and accent still need listening verification;
no reference transcript is fabricated, and the reference audio is not sent to cloud TTS.


## 安装应用与命令

源码开发使用上面的 `uv sync --locked --group dev`，应用以 editable 模式安装。
只使用应用时，在 Python 3.12 虚拟环境中执行 `pip install .`，或安装 wheel。

| 命令 | 用途 |
| --- | --- |
| `angeleno config` | 输出无密钥配置模板 |
| `angeleno preview` | 离线查看人物输入概况 |
| `angeleno chat` / `angeleno memory` | 文字交流与记忆管理 |
| `angeleno-voice check` / `angeleno-voice start` | 离线配置检查与语音启动 |
| `angeleno-trace` | 查看本地逐轮记录 |

配置、人物、网页与地方资料通过应用路径层定位。内置公开资源随 wheel 提供；GPU 服务仍在独立环境安装。

## 配置、人物与私有状态

```sh
angeleno config > .env
chmod 600 .env
```

随后为命令添加 `--env-file .env`。源码开发也可沿用默认的 `private/runtime.env`。

在私有文件中填写配置。环境变量优先于文件；可用 `--env-file PATH` 或 `ANGELENO_ENV_FILE` 明确选择文件。相对配置与数据路径以启动目录为基准，跨目录启动推荐使用绝对路径。解析器不执行shell、不插值，也不发现宿主账号文件。

- `ANGELENO_AUTHOR_PACK` 指向本仓作者包，或操作者提供的独立资源包；manifest中的正文必须在包内。默认完整 Jo 包来自随安装包提供的 `characters/jo/author-pack.json`；`examples/demo/` 是独立合成接线示例。不要把新包指向旧seed的记忆库。
- `ANGELENO_DATA_DIR` 默认 `private/state`，可指向仓库外的绝对路径（例如操作者的应用数据目录）。人物／用户记忆、费用及 ASR 账本分别保存；账本路径仍须位于该数据目录内。原始聊天、快照、账本和参考声音按私有数据保管。
- `ANGELENO_MEMORY_USER` 明确选择身份。首次创建需 `--memory-create` 或临时设置 `ANGELENO_MEMORY_CREATE=1`；创建后恢复为0，恢复时遇到不存在的身份、seed／schema不兼容或占用直接失败，不自动清库或改身份。
- `ANGELENO_TIMEZONE` 默认 `America/Los_Angeles`，只影响运行日期；不改作者事实或seed。

## 模型、费用与文字入口

配置 `DEEPSEEK_API_KEY`、模型、币种、输入／缓存／输出价格与来源、余额止损线及历史未知费用预留。价格从[官方页面](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)核对，按每百万token计；可以用已标明的保守上界做保护，但账本估算不能冒充钱包实扣。

每次模型请求前读取[官方账户余额接口](https://api-docs.deepseek.com/api/get-user-balance/)，缺配置、查不到余额或预留后不足即不发送请求。actor、工具续接和JSON维护共享入口；用量未知、失败或取消的预留保留。升级或重启时保留原账本，继续使用已有用量和未知费用预留。

设置 `ANGELENO_PAID_ENABLED=1` 启用模型调用。下列命令会产生 API 费用：

```sh
# 首次创建独立身份；此后的重开去掉 --memory-create
angeleno chat --env-file .env --memory-create
# 已有身份的后续交流
angeleno chat --env-file .env
```

文字 CLI 的 `ANGELENO_MEMORY_TOOLS=1` 恢复每轮最多一次记忆查询、最多两次 actor 请求，只查询当前身份的记忆。语音同时启用地方核对时的共用限额见下节。普通聊天不强制调用工具。维护使用同一 provider 的 JSON 输出，失败保留原文和旧状态。

CLI保留 `/memory`、`/sync`、`/private`、`/public`、`/forget ID` 与 `/exit`。`/sync` 可能收费；`/private` 停止保存新交流。删除已有内容必须显式操作，单独备份不会随删除自动消失。

## 语音服务与通话页

实际语音需要：配置好的模型账户、Paraformer密钥、有限的 `ANGELENO_ASR_MAX_SECONDS`、LiveKit服务，以及已经就绪的VoxCPM2 HTTP服务。ASR按发送音频时长独立预留，跨通话和进程累积，不存原始音频。请用该参数设置本地识别预算；它不是供应商余额。

先离线检查；该命令不创建记忆、不探测远端URL、不连接GPU：

```sh
angeleno-voice check --env-file .env
```

首次运行尚无身份库时，给下列 check／start 命令临时加 `ANGELENO_MEMORY_CREATE=1`；
check 仍只读，真正启动才创建本机用户状态。后续启动去掉该前缀；不复制他人的身份库，也不以初始化重置已有记忆。

`check` 验证本地配置；实际连接在启动时建立。设置 `ANGELENO_PAID_ENABLED=1` 并等待 TTS 服务就绪后，选择一种启动方式：

```sh
# 使用已经配置的LiveKit服务
angeleno-voice start --env-file .env
# 或由本次进程同时管理已安装的本地LiveKit executable
angeleno-voice start --env-file .env --livekit-bin livekit-server
```

自启模式只接受loopback `ws://` 信令地址。默认网页 `http://127.0.0.1:8767`，信令7880、媒体TCP7881／UDP7882、worker8081。端口占用直接失败，不杀占用者；Ctrl-C只关闭本次创建的子进程组，不安装系统服务或修改操作系统启动项。

跨设备访问需操作者已有的HTTPS与媒体网络配置：设置 `LIVEKIT_PUBLIC_URL`、`LIVEKIT_NODE_IP` 和明确的网页Origin；非本机配置拒绝演示key和弱secret。浏览器麦克风需要可信来源。当前仓库不自动配置Tailnet、开放公网或启用远端设备。

页面提供拨号、麦克风静音、字幕、来源卡片和挂断。部署后请在目标浏览器确认连接、播放、打断与重拨。

稳定地方读取可显式 `ANGELENO_PLACE_TOOLS=1`（默认 `0`），不回退人工fixture。主动Search/Fetch另由 `ANGELENO_WEB_ENABLED=1` 及[Web发送策略与账户保护](web-lookup.md)启用，优先于稳定读取；两者复用同一语音核心、定向卡片与页面，不另开查询服务。启动方式见[版本说明](trial.md#启动与停止)。每轮总计最多三次工具、四次模型请求；Search/Fetch可同类重复，其他工具每类一次。所有语音会话都需要本版token入口提供接收者metadata；旧版手工dispatch缺失身份时拒绝开始，不降级广播。

常规回答首音初始保护为15秒，查证回答为30秒，均从确认输入的同一单调时钟计算。短提示、查询完成或卡片到达均不重置期限。超时停止该轮，页面提示继续说话；模型、查询、TTS取消或媒体清理失败才停止通话。计时观察点位于服务端；浏览器与人耳听感的测量方法见 [voice lane](voice-lane.md)。`session.userdata['angeleno_timing']` 保留最近100轮的内存时间标记及锚点，不含原话，不自动写日志；程序输入使用 generation_start 回退锚点，与 ASR 确认时间分别标记。

首音出现后，如果模型生成尚未结束（包括提示音之后工具参数仍在流式生成），仍保留从原确认时刻起30秒的外层保护。该保护不猜对白语义，避免完整工具事件尚未到达时无限等待；已经完成生成的正常回复仍按实际播放结算。

## GPU与声音资源

GPU服务是独立可选环境，不由默认 `uv sync` 安装torch或下载权重。需要支持CUDA的Linux／NVIDIA环境及匹配驱动。`gpu/requirements.txt` 固定后端关键版本，其余依赖由目标环境解析；安装时按硬件匹配 CUDA 与 PyTorch。

```sh
uv venv --python 3.12 gpu/.venv
# 在 GPU 环境从 PyTorch CUDA 12.8 源安装匹配的 torch／torchaudio
uv pip install --python gpu/.venv/bin/python --index-url https://download.pytorch.org/whl/cu128 torch==2.7.0 torchaudio==2.7.0
uv pip install --python gpu/.venv/bin/python -r gpu/requirements.txt
```

操作者单独提供已下载的 `openbmb/VoxCPM2` 本地模型目录，并记录模型revision和校验；核对模型来源与许可。通过 `VOXCPM_MODEL` 显式指定目录，通过 `VOXCPM_REFERENCE` 指定有权使用的本地参考声音。模型和声音都不随Git分发；服务不会自动选择旧文件或下载缺失模型。

随仓默认人物包是 `characters/jo/author-pack.json`；公开示例 `examples/demo/` 仅用于接线测试。
参考音频不随人物包分发。将自己有权使用的参考放在 **GPU 主机**的 `private/voices/reference.wav`，
这只是路径示例；`VOXCPM_REFERENCE` 可指向 GPU 主机上的任意可读参考文件，包括源码目录外。worker 只核对逻辑资产和摘要，不要求复制声音到另一台机器。
当前服务走 VoxCPM2 的 **reference-only** 路径（`reference_wav_path`），不需要参考音频转写；
没有启用 continuation／combined 的 `prompt_wav_path` 和 `prompt_text`，也没有视频上传或自动提取音轨入口。
建议使用清晰、单一说话人、无背景音乐的 PCM16 WAV；现有真实参考的已验证输入是44.1kHz双声道PCM16 WAV，
内部解码／声道及采样转换由固定 VoxCPM2 loader 处理。其他容器和编码需自行确认 loader 兼容性；视频请先提取音轨。
请确认所选声音的使用权，并检查录音质量。

在 GPU 主机计算所选文件摘要，将结果填写到 worker 与 GPU 使用的私有配置：

```sh
python3 -c 'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("private/voices/reference.wav").read_bytes()).hexdigest())'
```

在 `.env.example` 对应项中设置 `VOXCPM_REFERENCE_PURPOSE=jo`、逻辑资产 ID `VOXCPM_REFERENCE_ID`
（例如 `local-reference`）和文件的 `VOXCPM_REFERENCE_SHA256`。GPU 在加载模型前验证文件可读性和摘要；
worker 在加入房间、启动 ASR 前核对 TTS health 中的用途、资产 ID 和摘要。缺配置或不匹配时启动失败，
不会替换为其他参考。更换声音后请更新摘要并重启 GPU 服务与 worker。`jo` 是人物用途标记，摘要用于校验文件一致性。

独立 TTS／PCM 诊断可使用 `VOXCPM_REFERENCE_PURPOSE=diagnostic`；health 和 PCM 响应头将标记该用途。Jo worker 只接受 `jo` 用途的参考。

```sh
# 填好本机资源路径后，显式加载私有配置启动GPU服务
uv run --no-project --python gpu/.venv/bin/python --env-file private/runtime.env python -m gpu.server
```

默认监听loopback18080，`/health`只在模型加载成功后就绪。配置 `VOXCPM_URL` 指向该服务。跨设备部署时，由部署者维护 GPU 服务及其连接。

句流结束／取消会显式关闭正在使用的TTS流，不依赖垃圾回收。可在worker启动配置中设置 `ANGELENO_TTS_PREFETCH_FRAMES=2`，用有界顺序预取覆盖下游消费停顿时的部分后句准备；默认 `0` 保持串行。仅允许0或2，错误配置在启动资源前报错。启用时最多缓存2个SDK音频帧及1个等待入队帧，单GPU合成仍串行，取消会丢弃旧轮待播帧并关闭句流；SDK内部缓冲不在这个容量承诺内。不新增起播等待、不改变15／30秒保护，也不能弥补长期RTF≥1的生成不足。实际收益取决于生成速度与下游消费节奏，请在自己的设备上对比。

显式 `VOXCPM_COMPILE=1` 选择官方编译路径，默认 `0`；GPU 与 worker 的该配置应一致。
编译需要 GPU 主机已有可用 C/C++ 编译器和匹配 Python 开发头文件；Ubuntu 24.04／Python3.12 的已验证组合为 `gcc`、`g++`、`python3.12-dev`。
这些是可选优化依赖，不由应用启动时自动安装；缺失则先使用默认 eager，不改驱动或降低生成质量。`/health` 的优化活动标记只有模型完成预热后才有效；
worker 要求它与本次配置匹配。`scripts/run_trial.py` 使用 `--tts-candidate compiled`，
独立预热成功后才开放用户加入；失败可回到默认 `eager`，不改变用户身份、音色或账本。

GPU 启动时对已验证参考音频编码一次，随后按同一已加载模型和参考内容摘要复用 VoxCPM2 的 reference 特征；每句仍创建独立生成状态，保留原10步、CFG与取消释放。缓存只在本进程内存中，不保存或上传；运行期间参考路径或内容改变会拒绝合成，须重新验证并重启。`VOXCPM_REFERENCE_CACHE=0` 可回退原逐句编码路径作诊断，默认 `1`。此项不自动打开 `VOXCPM_COMPILE`，编译仍按原配置。

音频性能观测默认关闭。仅在有界诊断中给GPU与worker进程显式设置 `ANGELENO_AUDIO_METRICS=1`；worker 另需 `ANGELENO_DIAGNOSTICS_FILE` 指向本地 `private/` 或配置的数据目录内的 0600 文件。GPU数值计量、worker新增块计量各自每进程最多2048条，跨句／请求共享，不随请求重置；到限只停止计量，服务和原生命周期事件继续。GPU输出仅数值JSON到当前私有进程日志；HTTP相关ID是每句新生成的随机值，不含对白或参考声音标识。记录生成／收块时序、PCM累计秒数、RTF（墙钟秒／音频秒）与本地队列；GPU推理RTF扣除输出队列等待，EOF排空不增加推理时长。worker同步push耗时不等于实际播放队列，SDK未公开的队列时长记为未知。预缓冲可跨句累积，不能把对应推帧精确归因到单句。

页面仅显式访问 `?audio_metrics=1` 时按秒读取音轨公开RTC统计，保存在本页 `window.angelenoAudioMetrics`，最多120条、120秒后停止，不上传。记录丢包、jitter、concealed samples和累计jitter-buffer平均等待；后者不是当前队列深度。不保存对白、音频、地址或用户／房间标识；缺失计数保持未知，挂断停止采样并拒收迟到结果。采样到期后仅停止观测，服务按原生命周期运行。

第三方代码和浏览器包来源见 [NOTICE](../NOTICE)。原创代码与文档使用 [MIT](../LICENSE)，NOTICE 列出的改编文件保留 Apache-2.0；Jo 作者材料的独立许可见 `characters/jo/NOTICE`。参考声音、模型权重与生成语音不由这些代码许可证授权。

可选记忆归因检查：设置 `ANGELENO_MEMORY_ATTRIBUTION_CHECK=1`，默认 `0`。它在提交新增用户事实／偏好／拒绝及相关episode前，用同一维护模型增加至多一次紧凑检查；明确纠正优先，不把角色说法当用户认可。针对已复现的单数指代→量化拒绝范围扩大，另有窄保守门，即使模型误判supported也保留未明，不自动扩大范围。原始交流和旧确认记忆保留；超时、取消、无效或不支持的判断不提交、不自动重试。两次维护调用共享既有超时预算，原5秒关闭上限不变；不阻塞前台语音，但会增加维护费用。范围检查目前针对有限的中文指代形式，启用后仍需关注误判；正式比较应保持两臂配置一致。


### 真实试用与开发资料分开

真实语音入口显式使用 `ANGELENO_MEMORY_PURPOSE=real_user`。技术语音入口使用 `development`、`dev-` 开头的独立身份及独立的 `ANGELENO_MEMORY_DATA_DIR`（例如 `private/development-state`）；比较器也显式标记开发存储。内存用途绑定后不可在两者之间悄悄切换，未标用途的调用不能打开已绑定的库。`ANGELENO_MEMORY_DATA_DIR` 仅分开人物／用户记忆，不改变原 `ANGELENO_DATA_DIR` 中的费用或 ASR 累计账本。

已绑定或隔离的库使用 memory schema3，旧 schema2 写入器必须拒绝，恢复时使用包含本修复的开发版本。明确有回执的开发记录可在一致备份后隔离：原文保留，原交付状态和依据另外保留，纯派生记忆停止供给；混合来源摘要不能自动整条当作合成丢弃。身份、seed 和真实历史不重置。原来真实说过的助手推断仍是助手发言，不能据此认证用户事实。

新通话尚未提出当前话题、输入仅为寒暄或附和时，不凭历史记录续办旧工具任务；明确续接、同通话内确认以及当前实际安排仍可正常使用工具。这个窄的寒暄识别不是通用意图分类器，也不是禁止闲聊中合理查证。


### Development tool decisions

For synthetic development sessions only, set `ANGELENO_TOOL_TRACE=1`,
`ANGELENO_MEMORY_PURPOSE=development`, and the existing `ANGELENO_DIAGNOSTICS_FILE` to a new 0600 file in the configured data directory or local `private/`.
The trace is off by default and records at most 256 events per process: proposed/validated calls, batch rejection,
local adapter start/end, and skipped calls with a controlled reason. Only tool names, hashed call IDs/arguments,
turn numbers and controlled statuses are retained; no raw parameters, responses, user speech, prompts or hidden reasoning.
`tool_adapter_start` is not evidence of an HTTP request or successful lookup. Existing Fetch HTTP events and the account
ledger provide separate network/billing evidence. Do not use missing old trace rows to invent a historical decision.


### Private per-turn trace

Set `ANGELENO_TRACE=1` and choose a new `ANGELENO_DIAGNOSTICS_FILE` under an existing local
`private/` directory or the configured `ANGELENO_DATA_DIR`. Capture is off by default. Files have mode 0600 and a 32 MiB limit.
Use `ANGELENO_MEMORY_PURPOSE=real_user` for a real trial, or `development` with the isolated
development store for synthetic evaluation. Each file has one purpose; temporary/no-save mode omits its content.

```sh
mkdir -p private/my-trial
# Add to .env, then start normally:
# ANGELENO_TRACE=1
# ANGELENO_DIAGNOSTICS_FILE=private/my-trial/operator-metadata.jsonl
angeleno-trace --env-file .env --file private/my-trial/operator-metadata.jsonl --purpose real_user
# Expand one turn's effective input, tool evidence and memory outcome:
angeleno-trace --env-file .env --file private/my-trial/operator-metadata.jsonl --purpose real_user --session <id> --turn t2 --expand input tools memory
```

The default view groups records by session and turn. It shows final user input, model timings and usage,
tool proposals and outcomes, source grades, generated text, adapter-confirmed delivery, card publication,
linked TTS/playout events and memory updates. Full prompts, source bodies and chunk timings are folded;
`--expand` opens them for local inspection. Input records include the effective messages and tools,
character/rule version, language, capabilities and selected context.

Read event stages separately: a proposal precedes validation and dispatch; adapter dispatch precedes HTTP;
server playout describes the server's observation, not whether a person heard the audio. An absent proposal
provides no hidden explanation for the model's choice. Missing terminal records indicate incomplete capture.
Large records carry truncation markers, and capture errors or limits leave the conversation running.

Trace files contain conversation text. Keep them private and remove them when no longer needed;
memory deletion does not remove trace copies. Credentials and common secret formats are redacted,
headers/cookies and microphone/reference/PCM audio are excluded. There is no external telemetry.
Memory and billing databases remain the authoritative state; trace records never feed back into prompts.

`ANGELENO_TRACE_REASONING=1` optionally captures bounded, redacted `reasoning_content` when the supplier
returns it. The provider uses `thinking: {type: disabled}`; the capture switch does not change that setting.
Synthetic evaluation uses the same fields in separate development files.

## 从源码运行型版本升级

先在原项目目录执行 `uv sync --locked --group dev`，再使用上表命令。
旧 `python -m her_agent ...` 改为 `angeleno ...`；`python scripts/run_voice.py ...` 可继续作为源码入口，推荐使用 `angeleno-voice ...`。
内部导入路径为 `angeleno` 与 `angeleno.worker`，无需再手动修改 `sys.path`。

已有部署保留原人物、用户身份、seed、数据库与账本。只要继续使用原启动目录和配置，`private/state` 仍指向原位置；
改从其他目录启动时，先将配置中的数据、账本与自定义资源路径改为原文件的绝对路径。
这次结构整理不会搬移或初始化已有运行数据。
