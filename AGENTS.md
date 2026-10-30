# Angeleno

做出能以人物视角聊 LA 当地生活、并按需查证现实信息的实时语音 Agent。用完整交谈情景判断人物贡献与地方能力，以实际语音判断轮次、打断、播放和延迟。

Purpose: [README](README.md) 与 [Intent](docs/intent.md)。
Main line: `refs/remotes/origin/main`
Settled record: [SETTLED.md](SETTLED.md)
Open items: https://github.com/OKFin33/Angeleno/issues
Verify: `make verify`。它串起 [baseline](docs/baseline.md) 的全部离线命令与 `git diff --check`，并含负向自检（坏 fixture 必须失败；`private/` 或 `.env` 被追踪即失败）。唯一 workflow 为 `.github/workflows/ci.yml`（路径过滤、timeout 15 分钟、无密钥与付费调用）。离线通过不代替真实语音或体验接受。
Authority: 在已受领范围内可修改、提交、push 私仓，维护 Issues 和 Draft PR；主线 merge、公开发布、未获授权的产品选择由 Owner 决定。收费调用、安装和部署须核对操作者当前有效授权，不由本文件扩权。

从 [Issue #1](https://github.com/OKFin33/Angeleno/issues/1) 的 Now、Verified、Next 接续工作；[Spec](docs/spec.md) 持有行为与验收，[baseline](docs/baseline.md) 持有实际代码和验证。目标、方案与实现状态分别维护；工作项不复制整份 Spec。

沿环境提供的 A\SDLC 方法推进，复用当前协作方法，不把个人方法快照或机器引导复制进仓库。当前入口优先于冲突旧说明，但不扩大授权或隐私边界。把新发现的剩余工作记录到所属 Issue，并继续独立可执行的部分。

优先复用已选核心，以最小查询、纠正／取消、音频与卡片、记忆一致的闭环取得证据。资料与工具为交流服务；不把人物虚构、用户陈述、现实事实和已交付对白混为一类。新模型或额外框架只有在实际缺口需要时再判断。

付费、设备或部署操作前，读取 ignored `private/operations.md` 的有效授权和暂停状态；缺少这份操作者记录时继续离线工作，不猜测密钥、预算或运行许可。`private/` 内的唯一成果不得因其被 Git 忽略而清理。私人聊天、记忆、原始日志、凭据与受限声音不提交；人物种子和已有记忆不静默迁移、重置或覆盖。Git 只承载可分享材料，公开素材与许可在交付前核对。
