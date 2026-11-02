# Angeleno

A character-driven voice companion for everyday Los Angeles.

Angeleno 让你与虚构人物 **Jo** 进行实时语音交流。Jo 有自己的生活背景、兴趣和偏好，可以顺着话题聊天，也可以按需查找现实信息。人物、记忆、来源卡片与可打断的语音共用一套会话核心。

**v0.1.1 默认使用英文，支持在通话中切换中文。** 设置 `ANGELENO_LANGUAGE=zh` 可使用中文默认配置。[版本说明](docs/trial.md#v011)介绍本次变化与验证范围。

## 功能

- 随仓提供完整的 [Jo 作者设定](characters/jo/author-pack.json)。
- 浏览器语音通话、实时识别、模型回复和流式合成。
- 跨通话记忆、用户纠正、临时不保存模式及独立的开发测试身份。
- 按需查询地方资料，使用 Search／Fetch 获取网页，并以卡片展示来源。
- 本地可选逐轮 trace，查看模型输入、工具调用、延迟、交付和记忆状态。

## 架构

```text
浏览器麦克风 ↔ LiveKit ↔ 语音 worker
                           ├─ DashScope Paraformer：语音识别
                           ├─ 人物与记忆核心 ↔ DeepSeek：回复与工具选择
                           │                   └─ Search / Fetch
                           └─ VoxCPM2 HTTP 服务：参考音色合成 → 浏览器播放
```

VoxCPM2 在使用者自己的 GPU 环境运行；识别和语言模型使用已配置的云服务。worker 与 GPU 可以同机，也可以分布在不同设备。跨设备部署需要受信任的 HTTPS 与可达的 LiveKit 媒体网络。

## 安装与离线检查

需要 Python 3.12、Git 和 [uv](https://docs.astral.sh/uv/)。页面测试另需 Node.js。worker 支持 macOS／Linux；Windows 使用 WSL。

```sh
git clone https://github.com/OKFin33/Angeleno.git
cd Angeleno
uv sync --locked --group dev
. .venv/bin/activate
make verify
```

`uv sync` 会安装应用与开发依赖，提供 `angeleno`、`angeleno-voice` 和 `angeleno-trace` 命令。
`make verify` 包含 Python、GPU 协议假模型、页面回归及有界安装包检查，无需密钥、麦克风或 GPU。

只安装应用时，可在 Python 3.12 虚拟环境中执行 `pip install .`，或安装构建出的 wheel；
安装后可在仓库外运行命令。

## 启动语音交流

完整配置步骤见 [安装与运行](docs/running.md)。

1. 用 `angeleno config` 生成配置模板，填写 DeepSeek、DashScope、LiveKit，以及模型价格与费用保护参数。
2. 使用随包 Jo 人设，选择自己的用户身份和 `ANGELENO_DATA_DIR`。源码开发默认使用 `private/state`；也可指定源码目录外的位置。
3. 在 CUDA GPU 环境安装 [GPU 依赖](gpu/requirements.txt)，准备 VoxCPM2 权重和有权使用的参考音频。
4. 启动 GPU 合成服务，检查配置，再启动 LiveKit、worker 与通话页。

```sh
angeleno config > .env
chmod 600 .env
# 填写配置并准备自己的参考音频后，首次创建本机身份：
ANGELENO_MEMORY_CREATE=1 angeleno-voice check --env-file .env
ANGELENO_MEMORY_CREATE=1 angeleno-voice start --env-file .env --livekit-bin livekit-server
# 后续启动同一身份时去掉 ANGELENO_MEMORY_CREATE=1。
```

`check` 是离线配置检查。真实启动需设置 `ANGELENO_PAID_ENABLED=1`，并提供已 ready 的 TTS 服务；示例默认关闭收费调用。默认网页地址为 `http://127.0.0.1:8767`。Ctrl-C／SIGTERM 停止启动器创建的子进程；独立 GPU 服务由部署者停止。

### 切换语言

默认 `ANGELENO_LANGUAGE=en`。通话中可以说 “Let’s speak Chinese” 或“我们换成中文聊吧”，明确切换后的语言会在本次通话中保持。新的通话按部署配置起步；中文部署使用 `ANGELENO_LANGUAGE=zh`。人物设定和既有记忆保持原样，中英文地名均可使用。

### 查看逐轮 trace

在运行配置中开启 trace，并在本地 `private/` 或配置的 `ANGELENO_DATA_DIR` 下选择新文件：

```dotenv
ANGELENO_TRACE=1
ANGELENO_DIAGNOSTICS_FILE=private/my-trial/operator-metadata.jsonl
```

```sh
mkdir -p private/my-trial
# 正常启动并交流后：
angeleno-trace --env-file .env --file private/my-trial/operator-metadata.jsonl --purpose real_user
# 展开某轮的模型输入、工具依据和记忆状态：
angeleno-trace --env-file .env --file private/my-trial/operator-metadata.jsonl --purpose real_user --turn t2 --expand input tools memory
```

默认视图区分生成文本和实际交付，折叠完整 prompt 与详细来源。trace 保存在本地，临时不保存模式会省略内容，开发评测与真实用户记录分开。文件包含对话资料，应按私有数据保管。开关、保留方式和可选供应商 reasoning 字段见[运行说明](docs/running.md#private-per-turn-trace)。

## 自备参考声音

仓库提供人物设定，参考声音和模型权重由使用者自备。

用 `VOXCPM_REFERENCE` 指定 GPU 主机上有权使用的参考音频路径，并设置用途、资产 ID 和 SHA-256。文件可放在源码目录外；`private/voices/reference.wav` 只是源码开发示例。GPU 服务和 worker 会核对文件声明；缺失或不匹配时启动检查会指出具体问题。

当前入口接受参考音频。视频素材需先在本地提取音轨。支持格式、模型安装和环境变量见 [GPU 与声音资源](docs/running.md#gpu与声音资源)。

默认使用 eager 推理；可显式选择编译优化并等待预热完成。不同硬件、模型版本和参考声音需要分别验证速度与音质。

## 数据与费用

Jo 是虚构人物。她的经历和观点来自作者设定；现实地点、营业时间与活动资料按来源及时效使用。

用户语音发送到配置的识别服务，模型上下文发送到配置的模型服务，TTS 在本地 GPU 运行。默认不保存原始通话音频。文本、记忆、账本和可选 trace 留在操作者配置的本地目录，请按私有数据保管。

模型与 ASR 分开计量。ASR 测试预算是部署者设置的本地保护，按发送的音频累计，包括静音；费用配置和账本说明见 [running](docs/running.md#模型费用与文字入口)。

## 验证范围

本版已通过离线回归、安装包检查、合成真实模型文字验证和英文识别样本检查。中文语音、取消与重拨已有既有链路验证；英文 TTS／RTC 的播放、打断重连及人工听感仍待实测。部署时应检查自己的参考声音、硬件和网络组合。

[试运行说明](docs/trial.md)保留具体版本与证据；同料比较工作见 [Issue #12](https://github.com/OKFin33/Angeleno/issues/12)。

## 目录

- `src/angeleno/`：人物、会话、记忆、模型、工具和命令入口。
- `src/angeleno/worker/`：ASR、TTS、LiveKit 与网页服务。
- `gpu/`：独立 GPU 服务及依赖。
- `characters/`、`data/`、`web/`：随安装包提供的公开运行资源。
- `docs/`、`tests/`、`examples/`、`scripts/`：文档、回归、示例与开发工具。

运行数据与源码分开；安装包不包含参考声音、服务凭据或运行数据库。

## 文档与贡献

- [安装与运行](docs/running.md)：依赖、配置、身份和服务。
- [版本与试运行](docs/trial.md)：变更与验证范围。
- [Intent](docs/intent.md) / [Spec](docs/spec.md)：产品目标与行为约定。
- [Web 查询](docs/web-lookup.md)：查询、来源与发送边界。
- [贡献约定](AGENTS.md)：开发与测试入口。

## 许可证与致谢

原创代码和文档采用 [MIT License](LICENSE)；从 talk-to-fengge 改编的文件保留 Apache-2.0，详见 [NOTICE](NOTICE) 和 `LICENSES/`。Jo 作者材料随项目分发，复用许可待定，见[随包声明](characters/jo/NOTICE)。参考声音与模型权重按各自许可使用。

感谢 talk-to-fengge、LiveKit、OpenBMB VoxCPM 及相关开源项目。
