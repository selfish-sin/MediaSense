# ideas.md — 想法与待决事项

## ASR 引擎选型候选（2026-09-12 调研，Exa）

> 背景：现役引擎（cloud_free XingChen 免费 / cloud MiMo ¥0.5h / firered / local）已覆盖
> 免费-付费-本地梯度；本文记录市场候选与结论，供"要不要换/加引擎"决策。

### 市场行情（2026-09）

| 候选 | 单价 | 速度 | 亮点 | 差距/限制 |
|---|---|---|---|---|
| XingChen Ultra（现役默认） | 免费 | 21.2s / 10min | 零幻觉、字数最全 | 术语同音字弱于 mimo |
| MiMo-v2.5-ASR（现役付费） | ¥0.5/h | 9.4s / 10min | 术语全对、最快 | — |
| Groq Whisper large-v3-turbo | **$0.04/h ≈ ¥0.29/h** | **300-450x 实时**（1h 音频 8-12s） | 全场最便宜 + 最快 | 需代理；25MB 上限；无标点规整/说话人分离；中文术语质量未实测 |
| 豆包 Seed-ASR 2.0 | ¥0.8/h（LAS 渠道）/ ¥1.6/h（语音直连） | 未实测 | 中文第一梯队 | 比 mimo 贵 60%+ |
| qwen3-asr-flash | ¥0.79/h | 未实测（上代 33s / 10min） | 情感识别、filetrans 12h/2GB | DashScope 原生接入 |
| Qwen3-ASR-1.7B（**已开源**） | ¥0（本地） | 待测 | 开源 SOTA、52 语言 + 22 方言 | 需本地部署；可作 FireRedASR 升级/对比候选 |
| OpenAI gpt-4o-mini-transcribe | $0.003/min ≈ ¥1.3/h | 60-80x | — | 贵于 mimo |
| AssemblyAI Universal-2 | $0.15/h ≈ ¥1.1/h | 144-240x | 英文强、说话人分离 | 中文弱 |
| Deepgram Nova-3 | $0.46/h | 快 | 流式低延迟 | 贵 |

### 结论

1. **免费档最优仍是 XingChen**（已默认），无需更换。
2. **比 mimo 更便宜的只有 Groq**（-40%）——但需代理，且 Whisper 系中文术语/标点大概率弱于
   mimo/XingChen，不值得为价格牺牲质量（除非实测过关）。
3. **豆包 / qwen3-asr 都比 mimo 贵**，而 mimo 术语已全对 → 无升级必要。
4. **值得补的两个洞（按需）**：
   - 说话人分离：XingChenASR-Diarize-V3.0（硅基流动同接口，改 `ASR_FREE_MODEL` 零代码）
   - 本地免费升级：Qwen3-ASR-1.7B（开源，替代/对比 FireRedASR，需实测速度与术语）
5. 效率层面：Groq 300x+ 只在超长素材上有感；本项目瓶颈在下载/上传，ASR 已非瓶颈
   （10min 素材 mimo 9.4s / XingChen 21.2s，任务墙钟是分钟级）。

### 待实测（若要推进）

- [ ] Groq whisper-large-v3-turbo 中文术语/标点质量（同素材 10min 对比 mimo/XingChen）
- [x] ~~Qwen3-ASR-1.7B 本地部署横评（vs FireRedASR 的 RTF / 术语）~~ → **2026-09-15 已实测，结论：不采用**
      （同素材 90s 中文，CPU int8：FireRed 18s/RTF 0.27/无幻觉；Fun-ASR-Nano 36~82s/RTF 0.5~1.0 +
      成段重复循环幻觉 + 中英混出；Qwen3-ASR 只下到一半即放弃。两者模型与代码均已删除，
      详见 `.trellis/spec/backend/transcription-chain.md` §5.1 与 task 09-15-local-asr-engine-registry）
- [ ] XingChen Diarize 版说话人分离效果

### 来源

- 火山引擎豆包语音计费 / LAS 大模型调用计费（Seed-ASR 2.0：0.8 元/h LAS 渠道、1.6 元/h 语音直连）
- 阿里云百炼 qwen3-asr-flash 模型价格（0.00022 元/秒；qwen3-asr 系列 2026 已开源）
- Groq / OpenAI / Deepgram / AssemblyAI / Google 公开定价 + Coval STT 2026 横评 + TokenMix Whisper 定价对比
- SiliconFlow 模型列表（XingChen Ultra / Diarize-V3.0 / Qwen3-ASR-1.7B / TeleSpeechASR）
