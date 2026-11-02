# Angeleno 开发约定

实现能以人物视角聊洛杉矶当地生活、并按需查证现实信息的实时语音 Agent。
产品目标见 [README](README.md) 与 [Intent](docs/intent.md)，行为与接受条件见 [Spec](docs/spec.md)。
当前工作见 [Issues](https://github.com/OKFin33/Angeleno/issues)，代码与验证范围见 [baseline](docs/baseline.md)。

## 开发与测试

使用 Python 3.12，运行 `uv sync --locked --group dev`；提交前运行 `make verify`。
它包含离线 Python、GPU 协议假模型和页面测试，无需真实 GPU 或服务密钥；页面测试需要 Node.js。
涉及语音体验的变更还应说明目标设备上的实际验证范围。

- `her_agent/`：人物编译、会话、记忆、模型与工具核心。
- `worker/`：ASR、TTS、RTC 与网页适配。
- `gpu/`：独立合成服务。
- `scripts/`：启动与开发命令。

在所属层做最小修改，为行为变化补回归。人物虚构、用户陈述、现实证据与已交付对白分别保留来源；
资料与工具服务当前交流。

## 运行数据与测试素材

按 [running](docs/running.md) 配置自己的服务账号、预算、模型与授权参考声音。
跨设备 HTTPS、媒体网络与 GPU 环境由部署者配置。

凭据、真实对话、运行记忆、账本和参考声音保存在本地 `private/`；`.env.example` 只放无密钥示例。
开发测试使用独立身份与存储，外部模型测试使用合成材料。升级时保留已有身份、seed、数据库和账本；
清理 ignored 文件前确认备份，避免删除本地唯一资料。

`scripts/verify_gate.py` 检查高风险路径和媒体文件，提交者仍需核对素材来源与内容。
合成 JSON、HTML、文本与图片可正常跟踪。合成音视频测试文件放在 `tests/fixtures/synthetic-media/`，
并在 gate 的精确允许清单中逐个登记；真实参考声音不作为测试例外。

## 提交与交付

使用分支和 PR，说明使用者会看到的变化、实际测试及兼容性影响。
受保护分支和发布遵循维护者流程。保留第三方署名与许可，人物材料遵随包声明；
声音、模型权重与生成内容分别遵循其适用权利与许可。
