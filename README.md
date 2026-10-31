# Angeleno

A character-driven voice agent for everyday Los Angeles, with real-time conversation and source-grounded local information.

与来自 LA 的虚构人物 Jo 语音聊天，听她聊熟悉的街区与当地生活，需要时一起查证具体信息。

Angeleno 探索的是一种有明确人物视角的语音交流：Jo 有自己的兴趣、偏好和熟悉范围；地方资料与受控查询帮助她回答现实问题。人物经历以设定和逻辑一致性为依据，现实地点、开放时间和活动信息则需要可核对的来源。

## 首版设计

- **地方生活交流**：中文为主，围绕 Jefferson Park 与 USC／Exposition Park 周边，聊街区日常、建筑与艺术文化。
- **对话中的查证**：按需查阅整理好的资料及登记来源；回答呈现简短来源卡片，保留核对时间与链接。
- **可打断的语音交互**：查询期间可以补充、纠正或换题；过时结果不应打断新话题，交流记忆以实际交付为依据。

## 开发与运行

当前实现已接入独立的人物／记忆核心、DeepSeek模型与记忆工具、语音worker、通话页和启动器。源码与运行入口均在本仓；依赖、配置、人物与声音资源的准备见 [独立运行说明](docs/running.md)。

```sh
uv sync --locked --group dev
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -m her_agent preview --env-file .env.example
.venv/bin/python -m her_agent.demo
node tests/test_call_page.js web/index.html
```

完整环境为 Python 3.12、macOS／Linux。以上使用合成数据，不需要模型账号、GPU或麦克风；公开示例是接线用人物“林”，不代表完整 Jo 人物包或人物体验。真实运行需操作者明确提供模型账户、人物包、LiveKit及GPU服务、参考声音和费用配置。

地方条目、登记来源、共用记忆／查证工具环和独立页面卡片已接入；语音设置 `ANGELENO_PLACE_TOOLS=1` 才启用[真实来源读取候选](docs/lookup.md#真实来源读取候选)，默认关闭。真实模型、语音、跨设备路由和整体体验尚未验收。离线检查与网络读取证据、限制见 [baseline](docs/baseline.md)。

产品依据为 [Intent](docs/intent.md) 与 [Spec](docs/spec.md)，工作进展见 [Issue #1](https://github.com/OKFin33/Angeleno/issues/1)，独立运行迁移见 [Issue #3](https://github.com/OKFin33/Angeleno/issues/3)。参与实现先读 [AGENTS](AGENTS.md)。第三方来源与许可见 [NOTICE](NOTICE)。
