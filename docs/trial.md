# 试运行与验证范围

本页说明当前可运行版本、验证证据及限制；首次部署从 [running](running.md) 开始。
产品范围和接受条件由 [Intent](intent.md)／[Spec](spec.md) 持有，v0.1 整体收口见 [Issue #1](https://github.com/OKFin33/Angeleno/issues/1)。

## 当前版本

[PR #30](https://github.com/OKFin33/Angeleno/pull/30) 已合入 main：
`eeae55b2f517d3209b2e9a0f3690643dac0fb277`，
[main CI 成功](https://github.com/OKFin33/Angeleno/actions/runs/41370501837)。
main 包含中文 v0.1.0、随仓 Jo 作者包与自备参考音频入口；未创建正式 Release。
本文后续维护性修整不改变以下真人试用的代码归属，不把旧开发分支作为新部署默认入口。

## 启动与停止

使用自己的服务账号、模型、预算和有权使用的参考声音，按 [running](running.md) 完成配置。
参考声音、实际聊天、记忆、密钥和账本均留在本地 ignored `private/`。

```sh
# 已完成配置后的首次身份检查与启动；之后恢复去掉 ANGELENO_MEMORY_CREATE=1。
ANGELENO_MEMORY_CREATE=1 .venv/bin/python scripts/run_voice.py check
ANGELENO_MEMORY_CREATE=1 .venv/bin/python scripts/run_voice.py start --livekit-bin livekit-server
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
- D1／D2／D3 各一对模型输出是已见、未评分开发材料；正式冻结、Owner 校准和保留集比较尚未完成，见 [Issue #12](https://github.com/OKFin33/Angeleno/issues/12)。
- 当前尚未完成全新环境安装与完整复现；也没有所有浏览器／网络／参考声音／语言的听感、音质或延迟保证。
  `USER.md` 与显式熟悉状态不是已实现功能。Jo 再许可及正式发布仍未决定，见随包声明。

历史阶段记录保留在 Git 历史与相关 Issue；其中旧暂停、费用批次、未合并状态和私有部署命令不是当前安装要求。

### 版本路线

**v0.1.0：中文首发准备。** 先交付可审核的 Jo 作者包、配置、依赖与启动说明，参考声音由使用者自备；
不将新环境安装或干净环境完整复现设为本轮候选形成的前置，也不宣称已通过该验证。
搜索提示小改仍后置，不混入本版或默认纳入下一版。代码已通过 PR #30 合入 main；公开发布仍由维护者另行决定。

**v0.1.1 默认英文（仅计划）。** 前提是 v0.1 中文候选交付并确认无阻碍；本版本不实施切换。
现有 ASR `paraformer-realtime-v2` 和 VoxCPM2 官方支持英语，不预设换供应商。
最小改动应统一可回退的默认语言配置，检查人物编译提示、开场／备用回复、工具结果表达、记忆维护及 UI 的中文默认冲突；
不必机械翻译全部人设，但须保持人物事实与风格一致。

当前源码定位：人物与维护提示在 `her_agent/author.py`、`her_agent/continuity.py` 及 `characters/jo/`；
ASR 在 `worker/paraformer_stt.py` 固定 `language_hints=['zh','en']`，结果语言标记为 `zh`；
口语／工具文字在 `her_agent/lookup.py`、`her_agent/web_lookup.py`，页面语言与文案在 `web/index.html`。
ASR 未来按实际调用显式选择 language_hints；英文／中英切换及专有名词须验证。
TTS 继续使用同模型与使用者自备参考，当前为 reference-only，不提供 prompt_text；若以后使用 continuation，
prompt_text 必须是原参考的准确转写，不能翻译后冒充转写。

验证英文分句、数字／时间／地名、人设自然度、声音一致性、首音及取消重开，不只看文字语言。
用少量合成多轮覆盖问候／偏好、地方信息与查证失败、地名数字、中途改说中文、取消与重拨；
先独立英语候选，验证后才改默认，保留中文显式回退。不宣称未经测试的参考英语发音或口音等价，
不预估无依据工时；当前未为此进行语言模型调用或英语音频验收。
官方参考：[ASR](https://help.aliyun.com/en/model-studio/paraformer-client-events)、
[VoxCPM2 跨语种](https://openbmb.github.io/voxcpm2-demopage/)、
[参考模式](https://voxcpm.readthedocs.io/en/latest/usage_guide.html)。
