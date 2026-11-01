# 首版候选的单一试运行交接

本页只把现有候选和运行说明收在一个恢复入口；范围与接受仍在Issue1/11/12和Spec。
**固定累计head以Issue11最近检查点和本机ignored交接清单为准**，不以PR数量计首版完成。
本候选分支`12-comparison-operator`叠加PR28 `d561c0b`；17–28均已包含，主线尚未合并。
PR28 head保持不变，其CI失败保留；本后继修首次检出缺tmp的测试setup，并补操作员接口。

## 只读状态与已有证据

2026-10-31核查：17–28各PR的base等于前一head（17基于main53e2598），全部Draft／未合并、GitHub报告mergeable。
17–27各固定head的ci成功；28的ci run41222335134失败于Verify。已在干净源码快照复现11项新测试
因`tmp/`未跟踪而setup失败；本后继显式创建该目录。后继CI与精确head由Issue11记录，不能把28失败改写成通过。
详细base/head/run ID保存在当前执行checkout的ignored交接清单，未读取受限Code Review日志。

已有：完整离线门禁／假SDK与页面验证；两次公开Search和一次Fetch成功（3990字符）；公开合成角色
真实多工具续接、缺历史正文时诚实回答。未验：Jo完整模型读正文／取舍、三对真实开发输出与Owner校准、
真实GPU／ASR／浏览器通话、分段延迟／实际播放和最多两轮Owner接受。
私稿外发审批拒绝保持停止；禁止从新比较CLI或语音通道试发来绕它。

## 当前执行checkout：离线与比较准备

先核`git status --short`为空、`git rev-parse HEAD`等于Issue11固定值；不要reset/clean覆盖现场。

```sh
UV_OFFLINE=1 make verify
.venv/bin/python -m her_agent.comparison_live --output private/evaluation/trial-offline-new.json
```

输出必须是新文件，已有回执不覆盖。此默认入口不读实际runtime.env／Jo或发网络。
真实开发接口／operator_v1见comparison.md；冻结输入可带execution_hold，必须解决现有审批问题后才执行。
恢复先读`tmp/issue12/README.md`和冻结配置／材料hash；无自动--resume或HTTP重放。
若取消／中断，保留回执／partial和原费用行；先核未知用量与未做段，不清账本、重跑整批或填生成结果。
SIGINT/Ctrl-C停止本进程，失败／取消关闭provider并清理本段新状态；私有回执留存。

## 获准窗口才准备真实运行落点

原私有配置／账本仍在原Angeleno checkout；隔离执行checkout不能直接拿外部DATA_DIR运行voice，
因为运行时要求状态位于该checkout的private内。**不复制密钥／数据库来解决路径问题。**
只读核得原checkout tracked树干净、main为8220b57（独立迁移后的旧代码），不是累计候选。
窗口与审批问题解决后，可在原checkout正常获取已审固定候选并临时detached运行，保留原main和ignored资源：

```sh
# 只在原运行checkout再次确认tracked干净后，由执行者按Issue11固定SHA替换下面参数
# git fetch origin <固定候选分支>
# git switch --detach <固定累计SHA>
# git status --short
# git rev-parse HEAD
```

本轮未执行这些操作。任何tracked变化／ignored冲突先保留并报告，不强行checkout。
原checkout的依赖须按同一uv.lock正常同步；GPU环境独立，不把本机venv搬到Linux。
原状态身份只显式选择，不重置；需要独立试用身份时明确新建，不能暗改seed或复用不兼容旧库。

## 必需配置名称

值沿既有私有配置，不在本文读出或复制：

- 人物／状态：ANGELENO_AUTHOR_PACK、ANGELENO_AUTHOR_VARIANT、ANGELENO_TIMEZONE、ANGELENO_DATA_DIR、ANGELENO_MEMORY_USER、ANGELENO_MEMORY_CREATE、ANGELENO_MEMORY_TOOLS。
- 模型／账本：ANGELENO_PAID_ENABLED、DEEPSEEK_API_KEY、DEEPSEEK_MODEL、ANGELENO_PRICE_MODEL、ANGELENO_BUDGET_CURRENCY、ANGELENO_PRICE_INPUT、ANGELENO_PRICE_CACHE_HIT、ANGELENO_PRICE_OUTPUT、ANGELENO_PRICE_SOURCE、ANGELENO_MIN_BALANCE、ANGELENO_BUDGET_CARRYOVER、ANGELENO_BUDGET_LEDGER。
- ASR／媒体：DASHSCOPE_API_KEY、DASHSCOPE_WS_URL、ANGELENO_ASR_MAX_SECONDS、ANGELENO_ASR_LEDGER、LIVEKIT_URL、LIVEKIT_API_KEY、LIVEKIT_API_SECRET、LIVEKIT_NODE_IP、LIVEKIT_FORCE_TCP、LIVEKIT_PUBLIC_URL、LIVEKIT_WORKER_PORT、ANGELENO_WEB_PORT、ANGELENO_WEB_ALLOWED_ORIGINS、ANGELENO_AGENT_NAME。
- TTS：VOXCPM_URL、VOXCPM_MODEL、VOXCPM_REFERENCE、VOXCPM_PORT、VOXCPM_COMPILE、VOXCPM_PREBUFFER_MS、HF_HOME、HF_HUB_OFFLINE。
- 主动Web（单独许可）：ANGELENO_WEB_ENABLED、ANGELENO_WEB_EGRESS=public-fields-v1、ANGELENO_WEB_REQUEST_LIMIT；原6次／0.03批次不重置，已用3／0.015。真实比较本地回放不消耗它。

## 用户只需协调的GPU窗口

工程准备齐后，申请Windows/NVIDIA可用30–60分钟并**明确解除原WSL暂停**；冷启未测，非一小时全验承诺。
只有该窗口内：核既有Linux/CUDA／本地权重revision与依赖、允许私下测试的本地参考声音，
启动loopback TTS、读health、合成公开短句并实听、测首个有意义音频及连续PCM、取消释放与下一轮；
媒体资源齐后再做同一候选浏览器插话／改口／卡片／挂断重拨及分段计时。
不涉及租GPU、充值、公开部署、声音／凭据上传、系统安全设置或自动merge。
本轮没有访问／探测／启动Windows，原暂停保持。若文件／依赖／路由仍未就绪，单列原因，不拿窗口当安装冒险。

在获准的原运行checkout，按既有running/voice-lane：

```sh
.venv/bin/python scripts/run_voice.py check --env-file private/runtime.env
# 获准Linux GPU环境、已有私有本地资源，单独服务进程
uv run --no-project --python gpu/.venv/bin/python --env-file private/runtime.env python -m gpu.server
# 已配置LiveKit时启动；需自管已安装服务时另加 --livekit-bin livekit-server
ANGELENO_PAID_ENABLED=1 .venv/bin/python scripts/run_voice.py start --env-file private/runtime.env
```

GPU命令不是在Mac执行；TTS跨设备只沿已有获准连接，不猜Windows主机名或新配凭据。
health不是出声证明。GPU服务在加载本地模型后，用同一参考声音与推理参数完整预热一句「你好。」并丢弃音频，成功才ready／接受请求；预热上限60秒，空音频、非有限采样、异常或超时均启动失败，不自动重试。卡住的CUDA调用不能安全线程强杀，超时后必须让失败的服务进程退出，不能复用为ready。此为启动保护，不延长普通轮次15秒／查证30秒保护；首个真实请求的延迟仍须实测。
ASR历史1139秒仅本地保守记录，窗口内核实际余额／账本；不足才给具体缺项，不默增额度。

停止：对voice启动器Ctrl-C／SIGTERM，只停其创建的子进程组；GPU服务单独Ctrl-C。
不按端口杀陌生进程。断后先确认owned进程退出／账本记录，再恢复同一SHA、同一明确身份及原账本；
需要创建身份的首轮显式设置MEMORY_CREATE，后续恢复为0，不自动清库。原运行checkout回退只在tracked干净时
`git switch main`恢复原分支；ignored状态／声音／回执保留，不git clean、不reset私有现场。

Owner只在三对真实输出后校准，并在可听候选上最多两轮判断；校准不是生成输出前置。
未解决审批识别问题、未获WSL恢复或未实听的项目仍属缺口，不能写为可验收完成。
