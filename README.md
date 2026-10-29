# Angeleno

A character-driven voice agent for everyday Los Angeles, with real-time conversation and source-grounded local information.

与来自 LA 的虚构人物 Jo 语音聊天，听她聊熟悉的街区与当地生活，需要时一起查证具体信息。

Angeleno 探索的是一种有明确人物视角的语音交流：Jo 有自己的兴趣、偏好和熟悉范围；地方资料与受控查询帮助她回答现实问题。人物经历以设定和逻辑一致性为依据，现实地点、开放时间和活动信息则需要可核对的来源。

## 首版设计

- **地方生活交流**：中文为主，围绕 Jefferson Park 与 USC／Exposition Park 周边，聊街区日常、建筑与艺术文化。
- **对话中的查证**：按需查阅整理好的资料及登记来源；回答呈现简短来源卡片，保留核对时间与链接。
- **可打断的语音交互**：查询期间可以补充、纠正或换题；过时结果不应打断新话题，交流记忆以实际交付为依据。

## 当前进展

产品处于早期开发阶段。首版 [Intent](docs/intent.md) 与 [Spec](docs/spec.md) 已接入；本仓提供一个可独立运行的离线会话与记忆基线，65 项合成检查通过。LA 查询、真实语音和来源卡片尚未在本仓实现或验收。

```sh
python3 -S -B -m unittest discover -s tests -v
python3 -S -B -m her_agent.demo
```

需 macOS／Linux 与 Python 3.10+；不需要模型账号或 GPU。演示使用预编写的合成回复，只验证连续性与请求组织。运行条件、来源与限制见 [baseline](docs/baseline.md)。

后续将提供真实通话 Demo、架构与取舍、同料助手比较及失败案例。开发接续见 [Issue #1](https://github.com/OKFin33/Angeleno/issues/1)；参与实现先读 [AGENTS](AGENTS.md)。
