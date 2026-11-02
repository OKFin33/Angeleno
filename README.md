# Angeleno

A character-driven voice companion for everyday Los Angeles.

Angeleno 让你与虚构人物 **Jo** 进行实时语音交流。Jo 有自己的背景、兴趣和偏好；她可以顺着话题聊天，也可以在需要时查找现实信息。项目探索的不是一个换了音色的通用助手，而是人物、记忆、现实资料与可打断语音如何协同。

**v0.1.0 以中文交流为主。** 默认英文计划在 v0.1 确认可用之后的 v0.1.1 实施；当前并未切换运行语言。当前候选位于 `nexus-integrated-trial-20261031` 开发分支，main 尚未更新；仓库访问仍遵循当前权限，未进行公开发布。

## 你会得到什么

- 可运行的人物与记忆核心，以及随仓提供的 [Jo 作者设定](characters/jo/author-pack.json)。
- 浏览器语音通话界面、实时识别、模型回复和流式合成。
- 按需使用的地方资料、Search／Fetch 和来源卡片。
- 可取消的回复、会话恢复，以及分离的开发测试与真实用户存储。

**仓库不提供参考声音。** 使用者需要自行准备有权使用的参考音频和模型／语音服务账户。人物设定的许可不等于任何真人声音的使用许可。

## 架构

```text
浏览器麦克风 ↔ LiveKit ↔ 语音 worker
                           ├─ DashScope Paraformer：语音识别
                           ├─ 人物与记忆核心 ↔ DeepSeek：回复与工具选择
                           │                   └─ 按需 Search / Fetch
                           └─ VoxCPM2 HTTP 服务：参考音色合成 → 浏览器播放
```

VoxCPM2 在使用者自己的 GPU 环境运行；识别和语言模型使用已配置的云服务。worker 与 GPU 可以同机，也可以分布在不同设备。跨设备 HTTPS、媒体可达性和网络连接由部署者配置，项目不会自动开放公网或配置 VPN。

## 先运行不收费的检查

需要 Python 3.12、Git 和 [uv](https://docs.astral.sh/uv/)。页面测试另需 Node.js。

```sh
git clone --branch nexus-integrated-trial-20261031 https://github.com/OKFin33/Angeleno.git
cd Angeleno
uv sync --locked --group dev
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -m her_agent.demo
node tests/test_call_page.js web/index.html
```

离线测试和 demo 使用合成数据，不需要模型密钥、GPU 或麦克风；它们不能替代真实语音验收。worker 支持 macOS／Linux；Windows 使用 Linux／WSL 环境，原生 Windows Python 不在当前支持范围。

## 开始真实语音交流

完整步骤见 [安装与运行](docs/running.md)，当前候选和恢复方式见 [试运行说明](docs/trial.md)。

1. 将 `.env.example` 复制到 `private/runtime.env`，填写自己的 DeepSeek、DashScope、LiveKit 配置，以及费用保护参数。不要把密钥放入提交。
2. 默认使用随仓 `characters/jo/author-pack.json`，创建自己的用户身份。实际聊天、记忆和账本保存在 ignored 的 `private/`，不要复制开发测试身份作为真人身份。
3. 在 CUDA GPU 环境安装 [GPU 依赖](gpu/requirements.txt)，准备 VoxCPM2 模型权重和自己的参考音频。模型权重也不随仓分发。
4. 启动 GPU 服务和 LiveKit，运行配置检查，再启动 worker 与通话页。

```sh
mkdir -p private/voices
cp .env.example private/runtime.env
# 按 docs/running.md 填写配置并准备自己的参考音频后，首次创建本机身份：
ANGELENO_MEMORY_CREATE=1 .venv/bin/python scripts/run_voice.py check
ANGELENO_MEMORY_CREATE=1 .venv/bin/python scripts/run_voice.py start --livekit-bin livekit-server
# 以后恢复同一身份时去掉 ANGELENO_MEMORY_CREATE=1，不换身份或重置已有状态。
```

配置检查不会调用模型或启动 GPU。真实启动需要显式启用收费服务；`ANGELENO_PAID_ENABLED=0` 是示例的默认值。默认本地网页地址为 `http://127.0.0.1:8767`，跨设备麦克风访问需要受信任的 HTTPS 与可达的媒体网络。

## 自备参考声音

将获得许可的音频放在 GPU 主机本项目的 `private/voices/reference.wav`，用 `VOXCPM_REFERENCE` 指定准确路径，并设置该文件的用途、资产 ID 和 SHA-256。GPU 服务与 worker 会核对这些声明；缺失、文件不符或音色信息不匹配时停止启动，不回退到其他人物的默认声音。

当前配置接受参考**音频文件**，不是视频上传入口。如果素材是视频，请先在本地、在有权处理的前提下提取音轨；不要把视频路径直接当作参考音频传入。准确格式、模型安装、摘要生成和环境变量见 [GPU 与声音资源](docs/running.md#gpu与声音资源)。

编译优化是显式候选，默认 eager 模式可回退。优化后的服务需要独立预热，完成后才开放通话，不应把预热时间算入用户首句响应。参考音色、硬件和模型版本不同，速度与音质都需要重新验证。

## 隐私、费用与内容声明

- **Jo 是虚构人物。** 人物经历来自作者设定，不证明模型真实去过某处；现实地点、营业时间和活动信息可能变化。
- 用户语音会发送到配置的语音识别服务，模型可见的会话上下文会发送到配置的模型服务。部署者应阅读并接受各供应商适用条款；不要把本地 TTS 误认为全链路离线。
- 默认不保存原始通话音频；文本与记忆的保存行为见运行说明。程序标记播放完成并不等同于确认用户听清。
- 模型与 ASR 费用分开记录。界面的 ASR 测试预算是部署者设置的本地保护，不是供应商账户余额，也不是本地语音合成额度。
- 不提交参考声音、真实对话、运行记忆、密钥或私有账本。替换音色不能绕过声音权利；本仓不授予任何真人身份或声音的使用权。

## 验证范围与已知限制

已验证的具体版本和步骤以 [试运行说明](docs/trial.md) 为准。开发候选已经覆盖离线回归、真实模型合成场景、语音取消与重开，以及一次八轮真实通话的程序播放完成记录。

这些结果不代表所有设备、语言、参考音色或网络都已通过。本轮未进行新环境安装或干净环境完整复现。仍需关注：

- 浏览器端听感、网络中继和音频生成波动；编译优化改善吞吐，但不能据此宣称没有卡顿。
- 工具使用由模型与上下文共同决定；自然闲聊不必搜索，不应把搜索次数当作质量指标。
- 识别可能出错，来源信息可能过时，模型记忆与事实归属仍有局限。
- 默认英文尚未交付；`USER.md` 和显式熟悉状态不是已实现功能。

## 文档与贡献

- [安装与运行](docs/running.md)：配置、账户、身份、GPU 与服务。
- [试运行说明](docs/trial.md)：版本、恢复、验证结果和限制。
- [产品 Intent](docs/intent.md) / [Spec](docs/spec.md)：产品范围与行为目标。
- [Web 查询](docs/web-lookup.md)：来源、调用与数据发送边界。
- [贡献与实现约定](AGENTS.md)：修改前阅读项目规则。

## 许可证与致谢

原创代码和文档采用 [MIT License](LICENSE)；从 talk-to-fengge 改编的文件保留 Apache-2.0，具体文件与其他依赖见 [NOTICE](NOTICE) 和 `LICENSES/`。Jo 作者材料的复用许可尚待 Owner 明确选择，见 [随包声明](characters/jo/NOTICE)，不应从代码许可证推定。模型权重、参考声音和生成内容不自动获得代码仓库的许可。

感谢 talk-to-fengge、LiveKit、OpenBMB VoxCPM 及相关开源项目。请保留各组件要求的署名与许可证。
