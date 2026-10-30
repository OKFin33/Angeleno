# 声线替换与真实语音检查

2026-10-30 · 本文只写替换方案、WSL 运行清单和真实语音检查表。不替换声线，不读取参考音频，不启动设备，不发起付费或 GPU 调用。真实语音按本表执行属于后续工作（[Issue #11](https://github.com/OKFin33/Angeleno/issues/11)）。行为依据是 [Spec](spec.md) 第 5、8 节，启动顺序依据是 [running](running.md)。离线检查通过不等于听到了声音。

## 当前边界

当前私有参考声音（the current private reference voice）在操作者私有资源中，是一段第三方转载的公众人物采访，授权没有记录。私下测试可以继续用它。任何公开 Demo、录音或生成语音之前，必须换成有授权的声线。这是公开发布的前置条件。

仓库许可证不覆盖人物材料、参考声音和生成语音，见 [NOTICE](../NOTICE)。Git 里不写这段参考音频的文件名、标题或说话人，不复制、不移动、不嵌入、不公布其校验值。本文不打开它，也不播放它。

`VOXCPM_REFERENCE` 只在未跟踪的私有配置里指向本地文件。公开材料不得改用这个变量的当前取值来顶替授权记录。

## 替换方案

三个选项都只是提案。选定一个之前，不改代码、不换文件、不改私有配置。

| 选项 | 以后会动什么 | 授权记录要证明什么 |
| --- | --- | --- |
| A. 仍走当前本地 VoxCPM2 HTTP，只换参考音频 | 私有配置里的 `VOXCPM_REFERENCE` 改指新文件。新文件留在 Git 外 | 该文件可用于克隆，并且点名了实际要发生的用途 |
| B. 改用自带许可的声音，不再克隆参考音频 | 可能不再强制本地参考文件。要改的现有文件见文末未决项 | 厂商或模型卡许可覆盖克隆以外的交付方式，并点名用途 |
| C. 使用一份新的已同意录音 | 新录音留在 Git 外，由私有配置指向它 | 书面同意单独点名私下测试、公开 Demo、公开录音、生成语音分发 |

选项 A 沿用现在的服务形状：GPU 进程从 `VOXCPM_REFERENCE` 读参考音频，HTTP 合成只接受已经配置的产品声音名。选项 B、C 若不能装进这个形状，执行阶段再改代码。本文不选 A、B 或 C。

### 授权记录

记录放在 Git 外，目录用仓库内的 `private/voice-licence/`，或仓库外的绝对路径。不要提交。记录可以在私有文件里写授权方身份；身份、原件和许可证正文都不进入 Git。

检查器认的字段：

| 字段 | 要求 |
| --- | --- |
| `schema` | `angeleno.voice_licence.v1` |
| `asset_kind` | `reference_clip`、`vendor_voice`、`consented_recording` 之一 |
| `covers` | 已允许的用途，取自 `private_test`、`public_demo`、`published_recording`、`generated_speech` |
| `cloning_explicit` | 布尔值。资产是参考音频而且要用于公开用途时，必须为真 |
| `model_weight_licence_noted` | 布尔值。公开用途要求为真，表示私有笔记写过所依据的权重许可 |
| `record_path` | 绝对路径。位于仓库内时必须在 `private/` 下，且不是 env 文件 |

`model_weight_licence_noted` 只表示笔记里有所依据的权重许可。它不是 Owner 对权重许可的接受，也不解除公开发布阻断。VoxCPM2 权重许可和参考声音授权分开记。权重是否足够，仍是未决项。

一份记录可以同时写下多项用途，也可以只写下已经同意的那些。没有点名的用途保持阻断。三项公开用途要合成一份授权还是分开三份，本文不决定。

### 接受步骤

下列顺序供以后的执行使用。本次停在第 1 步之前，一步也不做。

1. Owner 选定选项 A、B 或 C。
2. 按上表写好 Git 外授权记录。公开用途缺任何一项，该项不能发生。
3. 只在私有配置里把路径指到新资产。新资产不进 Git。当前私有参考声音不在本步骤删除，也不提交。
4. 私下测试可以改听新声线。当前私有参考声音在换掉之前仍只用于私下测试。
5. 用新声线按下一节检查表做真实语音，证据放在 Git 外。
6. 公开 Demo、录音或生成语音仍要等第 2 步覆盖该用途、第 5 步通过，并且 Owner 接受权重许可和这次发布。

### 什么拦住公开发布

- 作品仍由当前私有参考声音生成，或授权记录没有点名这次用途。
- 参考音频会被克隆，但记录没有写明允许克隆。
- 真实语音检查表还没有在替换后的声线上通过。
- 权重许可尚未由 Owner 接受。
- 只有本文、离线检查、字幕、卡片、写库或服务端播放事件。这些都不能代替听到的声音，也不能代替发布决定。

主线合并和公开发布仍由 Owner 决定。检查器认为记录结构完整，也不产生发布许可。

## WSL 运行清单

本节把 [running](running.md) 里的语音启动排成顺序，供以后在获准的 Linux 环境里使用。Windows 本机 Python 不在支持范围内；在 Windows 上时，这个环境是 WSL 里的 Linux。本次不进入 WSL，不安装 GPU 依赖，不执行下面的启动命令。

开始前阅读操作者私有的 `private/operations.md`。其中的暂停优先于本清单。文件不存在时，不猜测密钥、预算或运行许可，继续停在离线工作。本文不打开该文件。

### 前提

- 该 Linux 环境里有 Git、uv、Python 3.12。
- 应用依赖只用仓库锁文件安装。安装命令在 [running](running.md)。
- 本地 TTS 另要 NVIDIA GPU 对该 Linux 环境可见，并按 `gpu/requirements.txt` 与 running 文档安装独立虚拟环境。默认的应用同步不安装 torch，也不下载权重。
- 模型目录和参考音频都由操作者放在 Git 外，用下面的变量名指向它们。
- 私有配置文件是 `private/runtime.env`。环境变量优先于文件。解析器不执行 shell、不做插值。

### 变量名

只列名字。值只写在私有配置里。示例文件里的空值不是授权，也不抄进 Git。

应用、人物和费用：

`ANGELENO_PAID_ENABLED`、`ANGELENO_AUTHOR_PACK`、`ANGELENO_AUTHOR_VARIANT`、`ANGELENO_TIMEZONE`、`ANGELENO_DATA_DIR`、`ANGELENO_MEMORY_USER`、`ANGELENO_MEMORY_CREATE`、`ANGELENO_MEMORY_TOOLS`、`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`ANGELENO_PRICE_MODEL`、`ANGELENO_BUDGET_CURRENCY`、`ANGELENO_PRICE_INPUT`、`ANGELENO_PRICE_CACHE_HIT`、`ANGELENO_PRICE_OUTPUT`、`ANGELENO_PRICE_SOURCE`、`ANGELENO_MIN_BALANCE`、`ANGELENO_BUDGET_CARRYOVER`、`ANGELENO_BUDGET_LEDGER`

识别与通话：

`ANGELENO_ASR_MAX_SECONDS`、`ANGELENO_ASR_LEDGER`、`DASHSCOPE_API_KEY`、`DASHSCOPE_WS_URL`、`LIVEKIT_URL`、`LIVEKIT_API_KEY`、`LIVEKIT_API_SECRET`、`LIVEKIT_NODE_IP`、`LIVEKIT_FORCE_TCP`、`LIVEKIT_PUBLIC_URL`、`LIVEKIT_WORKER_PORT`、`ANGELENO_WEB_PORT`、`ANGELENO_WEB_ALLOWED_ORIGINS`、`ANGELENO_AGENT_NAME`

本地合成：

`VOXCPM_URL`、`VOXCPM_PREBUFFER_MS`、`VOXCPM_MODEL`、`VOXCPM_REFERENCE`、`VOXCPM_PORT`、`VOXCPM_COMPILE`、`HF_HOME`、`HF_HUB_OFFLINE`

`LIVEKIT_WORKER_PORT`、`ANGELENO_AGENT_NAME`、`ANGELENO_ASR_LEDGER`、`ANGELENO_BUDGET_LEDGER`、`VOXCPM_PORT`、`VOXCPM_COMPILE`、`HF_HOME`、`HF_HUB_OFFLINE` 已被代码读取，`.env.example` 还没有列出，见文末未决项。传入 `--env-file` 时，启动器把解析后的路径记入 `ANGELENO_ENV_FILE`，不必在私有文件里再写一遍。GPU 进程在未设置时把 `HF_HOME` 放到仓库 `private/cache`，并默认离线，不下载权重。

`ANGELENO_PAID_ENABLED` 在获准启动之前保持关闭。关闭时启动命令会拒绝开始。检查命令可以在关闭状态下看配置，不创建记忆，不探测远端 URL，不连接 GPU。

### 启动顺序

获准并且暂停已经解除之后才执行。顺序是：

1. 在仓库根目录做离线配置检查：`.venv/bin/python scripts/run_voice.py check`。需要指定文件时加上 `--env-file`，指向 `private/runtime.env`。
2. 使用本机合成时，先在独立 GPU 环境启动 `python -m gpu.server`。running 文档给出带私有配置文件的启动写法。`/health` 只在模型加载成功后表示就绪。进程绑定在 loopback。启动器不负责拉起它。
3. 再启动语音：`.venv/bin/python scripts/run_voice.py start`。若由这次进程一并拉起已经安装的 LiveKit 可执行文件，使用 `start --livekit-bin`，并传入该可执行文件的名字。此模式只接受 loopback 的 `ws` 信令地址。子进程顺序是：可选的 LiveKit、`worker.web_server`、`worker.main start`。
4. 浏览器打开 `ANGELENO_WEB_PORT` 上的本机页面。麦克风、拨号和真听属于 Issue #11。

端口占用时直接失败，不查找、不结束占用者。跨设备的 HTTPS、媒体网络和公网入口不由本清单打开。

### 停止顺序

1. 对启动器按 Ctrl-C，或向它发送 SIGTERM。它只结束自己创建的子进程组：先 SIGTERM，等待后仍在则 SIGKILL。没有被它创建的进程不动。
2. 单独停止 GPU 服务。启动器不拥有这个进程，也不会替它收尾。
3. 确认这次启动占用的端口已经释放。仍不按端口寻找进程来结束。

## 真实语音检查表

Issue #11 在获准的真实音频环境里逐项做。设备暂停未解除时不做。离线事件、合成播放测试和本文件都不能充当通过证据。

每项结果只取 `pass`、`fail`、`not_run`。`pass` 要写下人听到的内容、依据和 Git 外证据路径。`fail` 要写下原因。`not_run` 表示这次没有做。

证据目录用 `private/voice-evidence/<run-id>/`，或仓库外的绝对路径。笔记、时间戳和录音都放那里。不要提交。不要把参考音频、原始私聊、密钥、账户或授权方身份抄进 Git。检查器只读你交给它的那一份 JSON，不打开证据路径，也不打开参考音频。

服务端 `playback_finished` 表示传输层播放位置，不表示人听到了。生成全文、字幕、卡片和成功写库同样不能单独通过任何一项。

### 地名

在同一次私下通话里，让人物说出 `data/places/entries/` 里每一条的名称或已有中文别称。听的人记下实际听到的说法。

| 条目 | 应能对上的叫法 |
| --- | --- |
| Jefferson Park | Jefferson Park、杰斐逊公园、杰佛逊公园 |
| University of Southern California | USC、南加州大学、南加大 |
| Exposition Park | Exposition Park、博览会公园 |
| USC Fisher Museum of Art | Fisher、费舍尔艺术博物馆、费舍尔博物馆 |
| Natural History Museum of Los Angeles County | NHM、自然历史博物馆、洛杉矶自然历史博物馆 |
| California African American Museum | CAAM、加州非裔美国人博物馆 |
| Culver City | Culver City、卡尔弗城 |
| Sherman Oaks | Sherman Oaks、谢尔曼奥克斯 |

通过：每一条都能听成上表同一行，而不是另一行。笔记写下听到的说法。依据字段为 `heard_place`。

失败：听不清，听成另一行，或这个名字只出现在字幕或卡片上。

本表不新定读音标准，只核对听到的是哪一条目。

### 附和／插话

同一次通话里做三段观察：

1. 用户说一个多子句的句子，记角色音频是否在用户说完之前开始。
2. 查证等待时，用户补一个条件，或只给一个很短的附和。
3. 记下查证说明有几句，以及旧结果是否在新意图之后又响。

通过，依据字段为 `heard_turn`：

- 查证开始时最多一句短说明，然后等待。
- 用户补了条件之后，说出的结论跟着新条件。过期结论没有再播。
- 短附和之后，下一句仍对着最新意图。
- 用户这句话还没说完时，角色没有用回答内容盖住后半句。

失败：等待时持续说话填空；一边闲聊一边插入旧结果；补条件或换题之后又播旧结果；短附和被答成另一个新问题；用户没说完就被回答内容盖住。

角色没有主动发出「嗯」这类附和，不算失败。Spec 没有把主动附和写成必须发出的声音。

### 打断

用户在回答正在播放时开口。当前代码启用打断、关闭预生成；这只说明接线，不说明真听已经通过。

通过，依据字段为 `heard_stop`：

- 人听到正在播的声音停下。
- 停下的句子没有从切断处续上，也没有整段重播。
- 笔记只把听到的前缀当作已听到的部分。
- 同一次检查里挂断后再拨，旧队列没有续播。

失败：后半段仍然听得见；打断之后续上或重播；重拨后续播旧队列；只有日志写着中断，耳朵仍听见后半段。

### 实际播放

通过，依据字段为 `human_heard`：人从笔记里的输出装置听见这句回答。装置只写类别，例如耳机或扬声器，以及页面是否在本机浏览器。不写主机名、账户名或机器标识。

失败：证据只有生成全文、字幕、卡片、写库或服务端播放事件。它们可以附在笔记里，任何一项都不能单独通过。

### 分段延迟

时钟原点是用户这句话说完的时刻，记为 0 毫秒。四段都必须单独存在：

| 字段 | 从哪里到哪里 | 不能拿来顶替的信号 |
| --- | --- | --- |
| `asr` | 说完 → 识别确认 | 字幕出现、能量检测触发 |
| `model` | 识别确认 → 模型第一个文本片段 | 整段生成结束、工具返回 |
| `tts_first_audio` | 模型第一个文本片段 → 回答的第一段音频 | 查证开始时那句短说明 |
| `playback` | 回答第一段音频 → 人听见声音 | 服务端播放事件、字幕、卡片 |

通过，四项依据字段都是 `stage_clock`：

- 四个时长都是非负整数，并且等于上表两端时间戳的差。时间戳沿表中的顺序不回头。
- `tts_first_audio` 是回答的第一段音频。`answer_audio_not_filler` 为真。查证短说明不能充当结论延迟。
- `playback` 是人听见声音的时刻。`human_heard` 为真。字幕出现不能充当出声。
- `sample_count` 至少为 1。`claims_stable_percentile` 为假。

失败：缺段、负数、顺序回头、时长和端点对不上，或用上表右列的信号填了左列。

一次接通不能写成稳定百分位，也不能写成体验门槛已经达到。毫秒上限本文不设。

Spec 还要求能留下查询开始、查询结束、打断和音频停止的时刻。这些放在 `optional_marks_ms`：`query_start`、`query_end`、`interruption`、`audio_stop`。成对出现时，结束不早于开始。缺这四项不单独失败；写了却前后颠倒，记录无效。

## 离线检查器

`scripts/voice_lane.py` 检查一份 JSON 的结构。它不联网，不打开音频，不读 `.env`，不读 `private/` 里除你点名的那份 JSON 以外的文件。

```sh
.venv/bin/python scripts/voice_lane.py blank
.venv/bin/python scripts/voice_lane.py check PATH
```

`blank` 打印一张私下测试、八项都是 `not_run` 的空表。把空表抄到 Git 外再填写。`check` 退出码 0 只表示这份 JSON 结构成立：空表成立，如实写下的失败也成立。退出码 0 不表示八项声学通过。

公开作品记录要把 `voice_slot` 设为 `replacement`，`public_artifact` 设为真，`public_uses` 列出这次实际要发生的公开用途，并附上授权对象。八项都要通过，延迟对象要在场。`voice_slot` 仍是 `private_reference` 时，公开作品记录直接无效。

空表字段：

| 字段 | 空表中的值 |
| --- | --- |
| `schema` | `angeleno.voice_lane.v1` |
| `voice_slot` | `private_reference` 或 `replacement` |
| `public_artifact` | 布尔值。空表为假 |
| `public_uses` | 公开用途列表。非公开时必须是空列表 |
| `licence` | 授权对象或 `null` |
| `items` | 上面八个键各一项 |
| `latency` | 延迟对象或 `null` |

通过项只含 `result`、`basis`、`heard`、`evidence_path`。失败项只含 `result` 和 `reason`。未做项只含 `result`。证据路径必须是绝对路径；在仓库内时只能位于 `private/`，并且不能是 env 文件。仓库外的绝对路径可以。路径不必已经存在，检查器不创建它。

延迟对象在场时含 `sample_count`、`claims_stable_percentile`、`human_heard`、`answer_audio_not_filler`、`marks_ms`、`durations_ms`、`signals`。`signals` 固定为识别确认 `recognition_final`、模型首片段 `first_model_token`、回答音频 `answer_audio`、人听见 `human_heard`。其他信号写不进这个字段；只有字幕或查证短说明时，不要填一张假的延迟对象，把对应项写成 `fail` 并说明原因。

记录正文里不要粘贴密钥、令牌、`VOXCPM_REFERENCE` 赋值或私钥。检查器遇到这类文本只报告有凭证形状的值，不把该值打印出来。

## 未决

1. 选项 A、B、C 选哪一个，由 Owner 决定。未选定之前不执行替换。
2. 毫秒级体验门槛尚未冻结。本表否决缺段、回头和信号顶替，不设上限。
3. 角色主动发出附和声不是失败条件。若以后要把它写成必须行为，需另作决定。
4. 三项公开用途写在一份授权里还是分成三份，由 Owner 决定。记录里没有点名的用途继续阻断。
5. 模型权重许可是否足够，仍由 Owner 接受。`model_weight_licence_noted` 只表示私有笔记写过所依据的许可。
6. 执行替换时可能要改、而本次不能改的现有文件：`worker/agent.py`、`gpu/server.py`、`docs/running.md`，以及未跟踪的 `private/runtime.env`。当前 HTTP 合成只接受产品声音名 `jo`，并且必须有 `VOXCPM_REFERENCE`。这个声音名留不留，本文不决定。
7. `.env.example` 尚未列出代码已经读取的 `LIVEKIT_WORKER_PORT`、`ANGELENO_AGENT_NAME`、`ANGELENO_ASR_LEDGER`、`ANGELENO_BUDGET_LEDGER`、`VOXCPM_PORT`、`VOXCPM_COMPILE`、`HF_HOME`、`HF_HUB_OFFLINE`。要不要补进示例文件，本次不改现有文件。
8. 当前私有参考声音的文件是保留还是在执行阶段另作处理，本文不删除它。无论是否保留，都不进入 Git。
9. WSL 里 GPU 是否可见、驱动是否匹配安装说明，留到获准运行时核对。本文不探测。
