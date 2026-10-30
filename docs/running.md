# 独立开发与运行

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

## 私有配置与人物

```sh
mkdir -p private
cp .env.example private/runtime.env
chmod 600 private/runtime.env
```

在私有文件中填写配置。环境变量优先于文件；可用 `--env-file PATH` 或 `ANGELENO_ENV_FILE` 明确选择其他文件。解析器不执行shell、不插值，也不发现宿主账号文件。

- `ANGELENO_AUTHOR_PACK` 指向本仓作者包，或操作者提供的独立资源包；manifest中的正文必须在包内。完整Jo包由操作者私下提供，示例包可直接用于开发。不要把新包指向旧seed的记忆库。
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

`ANGELENO_MEMORY_TOOLS=1` 恢复每轮最多一次记忆查询、最多两次actor请求；这只查询当前身份的记忆，不是尚未实现的LA地方查询。普通聊天不强制调用工具。维护使用同一provider的JSON输出，失败保留原文和旧状态。

CLI保留 `/memory`、`/sync`、`/private`、`/public`、`/forget ID` 与 `/exit`。`/sync` 可能收费；`/private` 停止保存新交流。删除已有内容必须显式操作，单独备份不会随删除自动消失。

## 语音服务与通话页

实际语音需要：配置好的模型账户、Paraformer密钥、有限的 `ANGELENO_ASR_MAX_SECONDS`、LiveKit服务，以及已经就绪的VoxCPM2 HTTP服务。ASR按发送音频时长独立预留，跨通话和进程累积，不存原始音频。没有明确时长额度则拒绝开始。

先离线检查；该命令不创建记忆、不探测远端URL、不连接GPU：

```sh
.venv/bin/python scripts/run_voice.py check
```

检查通过只证明本地配置可用。`ANGELENO_PAID_ENABLED=0` 仍是安全默认值，实际启动还需要明确开启。服务就绪后，选择一种启动方式：

```sh
# 使用已经配置的LiveKit服务
.venv/bin/python scripts/run_voice.py start
# 或由本次进程同时管理已安装的本地LiveKit executable
.venv/bin/python scripts/run_voice.py start --livekit-bin livekit-server
```

自启模式只接受loopback `ws://` 信令地址。默认网页 `http://127.0.0.1:8767`，信令7880、媒体TCP7881／UDP7882、worker8081。端口占用直接失败，不杀占用者；Ctrl-C只关闭本次创建的子进程组，不安装系统服务或修改操作系统启动项。

跨设备访问需操作者已有的HTTPS与媒体网络配置：设置 `LIVEKIT_PUBLIC_URL`、`LIVEKIT_NODE_IP` 和明确的网页Origin；非本机配置拒绝演示key和弱secret。浏览器麦克风需要可信来源。当前仓库不自动配置Tailnet、开放公网或启用远端设备。

页面显示配置状态，不把它当作GPU／模型在线证明；拨号失败、识别／回复／播放错误和挂断均有停止路径。完整播放、打断、重拨和具体设备表现仍须真实验证。来源卡片与地方查证属于后续产品增量。

## GPU与声音资源

GPU服务是独立可选环境，不由默认 `uv sync` 安装torch或下载权重。需要支持CUDA的Linux／NVIDIA环境及匹配驱动。`gpu/requirements.txt` 固定既有后端的关键版本，其余依赖仍需记录目标环境解析结果；这不是已验证的全平台锁文件。

```sh
uv venv --python 3.12 gpu/.venv
# 在获准的GPU环境，先从PyTorch官方CUDA 12.8源安装匹配torch／torchaudio
uv pip install --python gpu/.venv/bin/python --index-url https://download.pytorch.org/whl/cu128 torch==2.7.0 torchaudio==2.7.0
uv pip install --python gpu/.venv/bin/python -r gpu/requirements.txt
```

操作者单独提供已下载的 `openbmb/VoxCPM2` 本地模型目录，并记录模型revision和校验；核对模型来源与许可。通过 `VOXCPM_MODEL` 显式指定目录，通过 `VOXCPM_REFERENCE` 指定有权用于测试的本地参考声音。模型和声音都不随Git分发；服务不会自动选择旧文件或下载缺失模型。

```sh
# 填好本机资源路径后，显式加载私有配置启动GPU服务
uv run --no-project --python gpu/.venv/bin/python --env-file private/runtime.env python -m gpu.server
```

默认监听loopback18080，`/health`只在模型加载成功后就绪。配置 `VOXCPM_URL` 指向该服务。启动器不自动恢复已暂停设备，假模型协议测试也不证明CUDA和真实音色可用。

第三方代码和浏览器包来源见 [NOTICE](../NOTICE)；项目原创建物的公开许可证仍待单独确定。
