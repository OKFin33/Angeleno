# 试运行与验证范围

本页说明当前可运行版本、验证证据及限制；首次部署从 [running](running.md) 开始。
产品范围和接受条件由 [Intent](intent.md)／[Spec](spec.md) 持有，v0.1 整体收口见 [Issue #1](https://github.com/OKFin33/Angeleno/issues/1)。

## v0.1.1

本次开发版包含以下更新：

- 默认英文对话与页面，保留中文部署配置和通话内明确语言切换；语言状态可跨近期历史裁剪保持。
- 中英文识别提示及英文分句处理，支持常见地名、缩写、时间和价格表达。
- 按实际记忆／查询能力生成一致的运行说明；普通助手比较臂使用单一、语言一致的设定。
- 区分历史资料与本轮查证；开发回放缺失作为覆盖缺口停止，真实查询失败仍按实际来源降级。
- 可选本地逐轮 trace：展开有效模型输入，查看工具及来源、延迟、生成／交付、卡片和记忆状态。

v0.1.1 已通过 [PR #32](https://github.com/OKFin33/Angeleno/pull/32) 合入 main。
可安装的 `src/angeleno/` 布局、安装包和稳定命令已通过 [PR #33](https://github.com/OKFin33/Angeleno/pull/33) 合入 main。
离线收口与比较报告 r1 说明已通过 [PR #34](https://github.com/OKFin33/Angeleno/pull/34) 和 [PR #35](https://github.com/OKFin33/Angeleno/pull/35) 合入；#35 的合并提交是 `6103c463d8530a0dced139161906cf13b5767af6`。
同料比较的冻结候选仍是 `4185b8eb29e8fa62ddc32300cc9db526f6704e15`，后来的说明提交不自动算进候选。
安装与命令见 [running](running.md)。

配置和 trace 用法见 [running](running.md)。`ANGELENO_LANGUAGE` 默认 `en`，设为 `zh` 可使用中文默认；
`ANGELENO_TRACE` 默认关闭。现有 Jo 作者文件、身份种子、记忆和费用账本保持兼容，恢复部署时沿用原私有状态。

验证覆盖离线状态／协议／页面回归、合成真实模型文字对话、缓存公开来源的工具／卡片过程与维护提交，
以及一段真实英文 ASR 样本。真实英文 TTS／RTC、打断重连和人工听感是下一阶段的验证重点。
trace 的真实模型样例验证了文字路径；语音观测接线由离线回归覆盖。

## 中文语音验证基线

[PR #31](https://github.com/OKFin33/Angeleno/pull/31) 已合入公开 main：
`57c8ddf971117574937a188c7f815842fa037624`，
[main CI 成功](https://github.com/OKFin33/Angeleno/actions/runs/41374964362)。
该版本提供中文 v0.1.0、Jo 作者包与自备参考音频入口。
以下真人试用记录对应各自标注的代码版本。

## 启动与停止

使用自己的服务账号、模型、预算和有权使用的参考声音，按 [running](running.md) 完成配置。
参考声音、实际聊天、记忆、密钥和账本留在操作者配置的本地目录。

```sh
# 已完成配置后的首次身份检查与启动；之后恢复去掉 ANGELENO_MEMORY_CREATE=1。
ANGELENO_MEMORY_CREATE=1 angeleno-voice check --env-file .env
ANGELENO_MEMORY_CREATE=1 angeleno-voice start --env-file .env --livekit-bin livekit-server
```

检查不连接模型或 GPU；真实启动要求显式启用收费服务，并核对已 ready 的 TTS 资产。
GPU 服务另行启动和停止；`run_voice.py` 的 Ctrl-C／SIGTERM 只清理自身创建的子进程，不结束他人程序。
跨设备 HTTPS、LiveKit 媒体和访问控制由部署者配置，不由本页自动开放。
ASR 测试预算按实际发送音频累计（包括静音），是本地保护，不是供应商余额或 TTS 额度。

`scripts/run_trial.py` 是已有私有 Tailnet 部署的维护工具，需要部署者保管的路由计划、基线和安装回执；
不是新安装的必要入口。既有部署的恢复命令、机器路径、连续状态和资源监督说明保存在部署者私有运行记录中。
新贡献者不需要这些记录，也不应复制他人的身份或账本。编译／eager 模式、自备参考、预取和归因检查选项见
[running](running.md#gpu与声音资源)；可选功能默认关闭与原限制不因文档整理改变。

## 已验证与未验证

- 真人 w16 基线为 [`c40717947ad7b88da9e26c289cef86ed82113ddb`](https://github.com/OKFin33/Angeleno/commit/c40717947ad7b88da9e26c289cef86ed82113ddb)：
  正确 Jo 参考、显式编译模式，8 个用户轮与 8 个回复标记完整交付，8 次正常程序播放结束、0 次中断。
  用户认可表达自然和所选声音，卡顿暂可接受；程序播放完成不证明每个字都被人耳听清。
- 此前技术验证分别覆盖实际中文 ASR→模型→TTS→native 媒体、取消／挂断重拨、私有浏览器合成 PCM，
  真实公开 Search／卡片以及 Fetch 失败后的诚实降级。受控迟到工具实验不冒充真实网页请求；失败与未完整播放场景仍保留。
- w16 没有 Search／Fetch 执行记录，不是搜索验收；未保存完整逐轮 HTTP／工具 proposal，不能据此推断隐藏模型原因。
- 后继已有真实／开发状态隔离、含糊输入不自动跨会话续旧任务、主动查证与人物表达修复，以及记忆归属保护。
  少量合成模型回归和离线状态测试不等同于后继版本已获真人整体接受。
- D1／D2／D3 各一对历史模型输出仍是已见、未评分开发材料，而且不在仓库里。候选、模型参数、同料来源和评分量表的离线冻结已经写上，见 [comparison](comparison.md)。Owner 校准没有做。H1–H5 标记为「起草时已读 D1–D3，仅作可见练习题，不作未见保留集」。未见保留集必须由没有读过 D1–D3 的人撰写。正式比较仍未执行。
- 干净环境的非 GPU 复现见下一节。没有所有浏览器、网络、参考声音或语言的听感、音质和延迟保证。
  `USER.md` 与显式熟悉状态不是已实现功能。Jo 再许可及 Release 仍未决定，见随包声明。

历史阶段记录保留在 Git 历史与相关 Issue；其中旧暂停、费用批次、未合并状态和私有部署命令不是当前安装要求。

## 干净环境复现（2026-11-15）

这次从没有虚拟环境的检出开始。Python 3.12.3，uv 0.13.8，Node.js v22.23.4。
`uv sync --locked --group dev` 新建 `.venv`，安装 77 个锁定包，退出码 0。没有服务密钥，没有参考声音，没有真实 GPU。

非 GPU 的 `make verify` 目标：

| 目标 | 结果 |
| --- | --- |
| `sync` | `uv sync --locked --group dev` 退出码 0 |
| `diff-check` | `git diff --check` 与已暂存检查均退出码 0 |
| `gate` | `python -B scripts/verify_gate.py --self-check` 退出码 0 |
| `tests` | `python -B -m unittest discover -s tests -v`：**612** 项，OK，18.039 秒 |
| `page` | `node tests/test_call_page.js web/index.html`：15 组页面回归通过 |
| `preview` | `angeleno preview --env-file .env.example` 退出码 0；Jo，4 份材料，`network_calls` 0 |
| `demo` | `python -m angeleno.demo` 退出码 0，三条离线 PASS |
| `install-check` | `python scripts/check_installation.py` 通过：wheel／sdist、三条命令、检出外运行，不含私有文件或音频 |

仓库根目录 `python -m pytest -q`：**630 passed**，1 条 `audioop` 弃用警告，501 个 subtests，22.86 秒。
早先参考环境的 pytest 是 624 passed。这次多出的 6 项是本分支的比较冻结和中文语言钉住测试。
`make tests` 使用 unittest，只收集 `tests/`，所以是 612，不是 630。

`make gpu` 另跑为 `pytest -q gpu/test_server.py`：**18 passed**，4 个 subtests，0.94 秒。
这是协议假模型，不加载权重，不占用 CUDA，也不能代替英文 TTS／RTC 或听感。

离线比较臂的数字见 [comparison](comparison.md#这次跑过的离线臂)。`scripts/check_comparison_freeze.py` 退出码 0。

这些命令又在干净提交 `48bb89a4dca51c473bd290112c815406ae8c4e2f` 上复跑，退出码和上表数字相同。公开合成包演练的 `observed_commit` 就是该提交，`tracked_diff_present` 为 false。

## 这次仍然不做

Zab 在 2026-11-15 认为 Angeleno 值得继续，但 GPU 部分这次大概做不了。下面这些没有做，也没有用离线结果代替：

- 真实英文 TTS／RTC、打断重连、人工听感，以及 E7 的播放中插话和挂断重拨。
- Owner 对三对开发锚例的校准，以及按该口径评分。早先三对模型输出不在本检出，没有重跑收费模型。
- 未见保留集的正式比较。H1–H5 标记为「起草时已读 D1–D3，仅作可见练习题，不作未见保留集」，不改写。未见保留集必须由没有读过 D1–D3 的人撰写。
- `comparison_live --run` 和在线 Search／Fetch。没有不可变模型版本，也没有把私有六组补充重新造进仓库。
- Jo 作者素材的再许可。代码和文档的 MIT、改编文件的 Apache-2.0 不自动覆盖人物素材。参考声音不随仓。
- 仓库公开、Release／tag 和正式发布。

Mayne 在委托范围内决定：本 PR 作为比较控制记录保留；正式比较仍未执行；H1–H5 不改写。仍要 Zab 决定的是人物素材再许可，以及是否公开或打 tag。英文语音仍等有 GPU 的时候再验。

<details>
<summary>版本验证记录</summary>

### 版本路线

**v0.1.0：中文首发准备。** 先交付可审核的 Jo 作者包、配置、依赖与启动说明，参考声音由使用者自备；
不将新环境安装或干净环境完整复现设为本轮候选形成的前置，也不宣称形成该候选时已经通过。
2026-11-15 的收口另做了一次干净环境非 GPU 复现，结果写在上文，不把 v0.1.0 形成时改写成已经复现过。
搜索提示小改仍后置，不混入本版或默认纳入下一版。代码与文档已通过 PR #30／#31 合入公开 main；Release／tag 仍未创建。

**v0.1.1 默认英文。** 已实现统一 `ANGELENO_LANGUAGE=en|zh`、默认英文人物回复与页面、
英文优先的双语 ASR 提示／结果标记及英文分句，人物原稿、种子、声音参考与搜索策略不变。
安装和中文回退见 [running](running.md#interaction-language-v011)。

离线测试覆盖中英文角色／记忆兼容、显式配置与 SDK stream 覆盖、16k PCM 参数、英文小数／时间／缩写跨块分句、页面静音重拨。
一次真实 DeepSeek 合成开发验证完成问候、人物饮食偏好、来源未覆盖时的回应及用户要求切回中文；
共5次模型请求、usage完整，无真实用户历史或实时Web请求。来源是明确的 replay fixture，不是新网页事实。
这证明语言接合，不证明所有人设细节、记忆语义或查证表达无误，也不改变正式同料比较未完成的状态。

另一次真实 DashScope 验证识别了2.067秒本地合成英文问候，配置标记为 en；Jo 被识别为同音的 Joe，原转写保留，未人工替换。发送含尾部静音，按原 ASR 保护预留4秒；这不是 RTC 端到端验证。

本次两次观测 GPU 97%／98% 时未启动测试、不结束他人进程。真实英文 TTS／RTC、取消重开和人工听感仍待 GPU 空闲验证，
不能用此前中文 w16 或本次协议测试替代。现有参考为 reference-only，沿用本地文件，未外传至音色服务。

官方参考：[ASR](https://help.aliyun.com/en/model-studio/paraformer-client-events)、
[VoxCPM2 跨语种](https://openbmb.github.io/voxcpm2-demopage/)、
[参考模式](https://voxcpm.readthedocs.io/en/latest/usage_guide.html)。

</details>
