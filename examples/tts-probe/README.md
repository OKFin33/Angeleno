# 独立 TTS 传输探针

固定 PR29 接口的本地准备。默认只打印三项 not_run 计划，不联网：

```sh
.venv/bin/python scripts/probe_tts.py
```

只有服务、声音及连接均已获准且就绪后，操作者才可显式运行：

```sh
.venv/bin/python scripts/probe_tts.py --run \
  --url http://127.0.0.1:18080 \
  --output private/validation/tts-probe-NEW
```

没有地址默认值；只接 loopback 或已有获准路由的本机落点，不创建SSH隧道。
不读取env、人物、参考音频、模型配置或费用账本，不启动服务，不自动重试或恢复。
三个公开输入原样分别POST，不使用LiveKit适配器的按句拆分及重试。
PCM样本边界复用 `worker.pcm.PCMResampler`。

依次记录正常流、收到至少9600字节完整PCM后关闭的流、取消后新请求。
每步前核health的ready、48kHz和busy；取消后有界观察busy=false，再发送独立恢复请求。
是否曾观察busy=true另行记录；没看到true不能声称发生了状态转换或取消导致锁释放。
任一步HTTP失败、超时、坏PCM、服务已忙或取消未真正触发即停止，剩余项保留not_run。
`--timeout`默认30秒，只接受有限正数；每次health／合成及取消后等待均有界，
它是诊断停止界限，不是产品延迟接受门槛。

输出为新目录内receipt.json及三条请求各自的48kHz单声道s16le PCM。
first_pcm_ms是请求至首个完整样本，不是模型首token至首音，也不是人听见的时刻。
客户端关闭、之后busy=false及新请求完成分别记录；没有服务器取消确认协议，
因此server_cancel_acknowledged始终未知，不能把它们写成真实打断或浏览器播放通过。
human_heard保持null，实际播放、音色和语义内容须另外实听。
回执只存必要协议字段；不保存响应正文、任意响应头或未知异常原文。

Ctrl-C关闭本次HTTP会话、保留partial PCM／回执并停止；不杀服务或陌生进程。
取消后若服务仍忙，在诊断界限停止，不按端口杀进程，也不重发合成。
保留失败目录，下一次必须选新输出路径。生成音频留在ignored目录，不作为公开材料。
