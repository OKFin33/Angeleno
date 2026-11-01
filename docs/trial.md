# 首版候选的单一试运行交接

本页是 Jo 产品试用的恢复入口；范围与接受仍在 Issue1/11/12 和 Spec，不以 PR 数量计完成。

## 当前恢复点（2026-11-01）

已发布开发分支为 `nexus-integrated-trial-20261031`，本次生命周期修复的基底 head 为
[`911e1fbcbabe5a7094afefba0b8a575f45f5d459`](https://github.com/OKFin33/Angeleno/commit/911e1fbcbabe5a7094afefba0b8a575f45f5d459)；
后续恢复须核该分支最新已交接 head，不回退到下文历史 PR28／旧 main。
Nexus 唯一项目目录是 `/Users/nexus/project/GitHub/OKFin33/Angeleno`；
旧任务目录只作归档，不从其中启动，也不另建运行 checkout。

1. 优先利用云端核发布分支与本页；需本机恢复时，仍由原运行执行者在上述唯一目录核
   `git status --short`、`git rev-parse HEAD` 与既有私有运行交接。
   保留原 `private/`、`private/state`、人物／用户身份、seed、费用及 ASR 账本；
   不复制凭据或数据库，不 reset／clean，不创建替代身份或重置累计用量。
2. 沿用用户已选、已在 Nexus／KFC 就位的无 BGM Jo 参考文件，以原私有配置中的路径、
   资产 ID 和摘要为准；不选旧默认声音或 diagnostic 文件。保留
   [e2c7322 的 Jo 资产绑定](https://github.com/OKFin33/Angeleno/commit/e2c7322eb97f3b3d0d8fa766527e6732b8016cbc)：
   `VOXCPM_REFERENCE_PURPOSE=jo`、`VOXCPM_REFERENCE_ID`、`VOXCPM_REFERENCE_SHA256`
   必须与 GPU health 的已验证声明一致；不把文件名或 health 当作实听通过。
   参考文件名、路径值、摘要值和凭据只留在原私有交接，不抄入 Git。
3. 保留 [af1dbcb 的逐请求价格快照](https://github.com/OKFin33/Angeleno/commit/af1dbcb30b60740023f9b041991aca319af97ac8)
   和原账户账本；旧行、未知预留及既有请求的价格不重写、不重新定价。
4. 交接时服务均已停止；启动前核实际进程及 GPU 持续负载，低负载可依现有授权使用，高负载不抢占。
   GPU 历史用时只作记录，不作为旧累计时长停工门槛；保留原 ASR 音频预留与费用账本，
   不因重开进程或换目录重置用量。启动／预热不占可发送音频余量；实际账户拒绝或本地音频预留用尽时停止相应能力。
5. 先执行下一节不带 `--run` 的 product 预检，再由原资源 runner 在获准窗口内启动。
   可选 `ANGELENO_TTS_PREFETCH_FRAMES=2`（默认 `0`，只允许 0／2）；
   可选 `ANGELENO_MEMORY_ATTRIBUTION_CHECK=1`（默认 `0`，会增加维护费用）。
   两项均不默认打开、不扩大许可，也不代表声音连续性或记忆语义已验收。
   配置与限制见 [running](running.md#gpu与声音资源)。

下文有界 product 入口仍适用；标为历史的候选、路径、额度和暂停描述不是当前状态或新的启动授权。

## 有界完整产品试用入口

`scripts/run_trial.py`接回既有私有媒体supervisor：`--mode product`在同一会话启用Jo、真实ASR/TTS、
DeepSeek Search/Fetch及定向来源卡片。默认`technical`仍关闭Web；未传`--run`只读核配置，不启服务或调用API。
这是恢复入口，不是完整产品联合验收或Owner实听通过。后文2026-10-31状态保留为历史，
当前候选、实际余额和设备授权仍以操作者原交接为准，不把旧批次封顶追认为新许可。

在**保有原private状态**的干净运行checkout切到已审候选，同一`uv.lock`同步依赖。
不要从隔离代码工作树复制凭据／数据库，也不要把未审实验提交带入候选。先于任何GPU启动执行：

```sh
.venv/bin/python scripts/run_trial.py --mode product --env-file private/runtime.env
# 再只读核现有Serve与端口；任何配置／库存错误先处理，不占GPU窗口
.venv/bin/python scripts/run_trial.py --mode product --env-file private/runtime.env --check-host
```

入口消费已有本地路由计划、Serve基线及官方LiveKit安装回执，CLI可显式指定其路径；它们不含在Git中。
预检只读验证现有身份与seed、原费用账本账户／价格、ASR原累计保护、二进制指纹和媒体访问范围。
不创建身份、账本或ASR额度。`--check-host`不验证远端余额、GPU健康、TLS签发或用户实听。

原资源 runner 确认 GPU 持续低负载后，以父进程管道管理 TTS／SSH，再启动以下 product 入口；它不直接拥有 GPU／SSH或18080。
原私有用户启动器统一执行此接合，等待准备与闲置分别计时，不再用旧540秒墙钟截断正常通话。
使用同一原身份、原账本和现有凭据；持久配置的paid/Web默认继续为0，仅子进程显式启用。
product模式采用当前已获准的合理账户使用策略`ANGELENO_WEB_REQUEST_LIMIT=account`，
历史请求与未知预留均保留，不制造新的金额／次数批次；每请求余额核查、账户拒绝、供应商403与单轮保护仍生效。

```sh
trial_session_id=$(.venv/bin/python -c 'import uuid; print("product-" + uuid.uuid4().hex[:12])')
.venv/bin/python scripts/run_trial.py --mode product --env-file private/runtime.env --run \
  --lifecycle --output "private/validation/$trial_session_id/supervisor.json"
```

显式编译候选：在原资源启动器选择 `--tts-candidate compiled`，确认其完成有界GPU预热后，
向本入口传同一参数。默认 `eager`；本入口不代为启动GPU，也不因编译改变15/30秒交互保护、
音频缓冲或ASR原账本。操作者启动器分别留160秒GPU准备和195秒总准备；普通模式保留80/95秒。
预热前不发出用户token或创建ASR；worker会核已选Jo资产和实际优化就绪状态，不能以请求了编译冒充成功。
失败只清理本次资源；保留原私有状态，以新会话ID选择 `--tts-candidate eager` 即可恢复，无自动重复尝试。
各模式ready后才进入用户连接／音频预留生命周期。编译模式已见源端性能收益，音质和真实浏览器听感仍需体验，
不得将编译前后PCM视为逐字节相同。

浏览器入口取既有路由计划的HTTPS9443；同机worker走内部ws7880，浏览器走WSS9444及已批TailnetUDP7882。
只增量运行已批私有Serve路由，保留原443/8443与Funnel，不开公网、不启ICE/TCP通配。
product模式拒绝任何`ANGELENO_TRIAL_*`测试hook，不使用E6合成工具代替真实查询，也不强迫演员调用工具。
需要实际查询覆盖时，记录是否自主选择Search/Fetch、真实工具回执与卡片接收；没有选择就标未覆盖，不自动刷输入。

`--lifecycle` 使用原本地 ASR 音频预留账本：发送的静音也计入，按流向上取整；
它是语音识别测试的本机自设保护，不是本地 TTS 合成额度、供应商账户余额或已结算费用。
页面明确显示识别测试预算，余量不足60秒时提示；保留原1200秒保护及历史消耗，不把它当作用户规定的产品硬限。
媒体启动检查最多90秒（编译GPU准备另按上述候选时限）；ready 后无人加入300秒、连接后连续300秒无音频预留进展会结束；挂断后留5秒重拨宽限，
用尽后留3秒收尾。正常传输中的通话没有额外540秒墙钟截止。临时静音未达闲置限时不会结束。
旧 `--stop-at` 仅供固定期限技术诊断（UTC，最多十分钟），不与 `--lifecycle` 混用。
父启动器通过 `--owner-stdin` 管道监督本进程；父退出即收尾。GPU使用本实例健康租约；SSH纯转发由本机guard以90秒心跳监督，不依赖远端PowerShell读取标准输入。
父管道EOF、失联、异常退出均结束本任务转发；
监督丢失会回收；它不是用户可聊时长。未连接的等待与静音闲置仍有上限，不无限占用 GPU。
Ctrl-C/SIGTERM和组件失败都清理本次拥有的子进程组、临时密钥配置及Serve增量，
清理回执须证明子进程退出和Serve恢复到原基线。TTS/SSH由原资源runner的finally单独收尾，
不杀端口占用者、不删除原private资源。ASR与GPU历史额度不扩大；关停后不能声称页面仍可试用。
HTTP使用独立daemon请求线程和2秒socket读取超时；服务循环未进入时不等待`shutdown()`。
HTTP shutdown／socket close各最多等待1秒，超时记为清理未完成，仍继续退出自有子进程及删除临时密钥配置，
不把HTTP关闭失败误报为全部清理成功。

## 历史记录（2026-10-31，勿作为当前恢复指令）

以下保留当时的证据、比较准备及运行说明；其中旧 checkout／detached 切换、旧额度与暂停状态
由本页「当前恢复点」及原私有运行交接取代。通用配置与运行规则见 [running](running.md)。

### 只读状态与已有证据

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
- 主动Web（单独许可）：ANGELENO_WEB_ENABLED、ANGELENO_WEB_EGRESS=public-fields-v1、ANGELENO_WEB_REQUEST_LIMIT；上限为原账本累计总数，续批按当前许可显式增加，不清零旧请求或未知预留。原6次／0.03及早期3次／0.015均为历史检查点，实际余额／计数／许可核操作者原记录。真实比较本地回放不消耗它。

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

本地边界诊断默认关闭。需要取证时，操作者可显式设置
`ANGELENO_DIAGNOSTICS_FILE=private/validation/<本次目录>/operator-metadata.jsonl`；父目录须已存在。
只追加0600的私有元数据文件，不输出提示、对白、音频、凭据或自由查询。
TTS HTTP EOF、句流结束和播放完成分别记录；取消／异常不能当正常结束。
它不改变15／30秒保护，不构成运行或付费许可，也不补造旧回执缺失的时间。

语音入口按当前角色自然回答，查证后先给简洁的关键答复、判断与必要的不确定性。
工具结果的 `response_presentation` 由程序标明是否有当前独立卡片通道；有通道时，
详细来源、链接及多套时间表留在卡片，口语不重复整张卡，也不声称用户已经收到或看过。
无卡片通道时仍须口头交代必要依据；用户明确要详细听取、逐条比较或朗读来源时可展开，
不能为了简短丢关键事实、例外或冲突。此为语音专用呈现说明，不截断 TTS，不改文字比较或作者正文。
`examples/voice-presentation.development.json` 保留已见 w08 中断前缀与离线反例，
只能证明规则、证据和通道说明接入请求，不能证明模型已经改善或完整语音通过。
