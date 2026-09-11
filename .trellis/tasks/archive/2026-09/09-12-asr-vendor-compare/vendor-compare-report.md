# ASR 引擎横评：百炼 Qwen-ASR / 小米 MiMo-ASR / SenseVoice / 本地 FireRedASR

> 2026-09-12 | 同一素材：BV1bH4y1r75q《经络辨证和针灸处方》**前 10 分钟**
> 三家云端走完全相同的静音切分管线（88 片）、相同并发 8；本地 FireRedASR 数据取自
> 09-11 对比（同素材）。密钥不入库不入文档；证据在 evidence/ 目录。

## 一、总表（速度 / 质量 / 成本）

| 指标 | 小米 mimo-v2.5-asr | 百炼 qwen-audio-3.0-asr-flash | 云端 SenseVoice（现主力） | 本地 FireRedASR2 |
|---|---|---|---|---|
| 10min 素材总耗时 | **9.4s（96x 实时）** | 33.0s（带 429 重试） | 848.4s（0.71x 慢于实时） | 168.8s（3.6x） |
| 每片中位延迟 | **3.9s** | 13.8s | ~108s | — |
| 分片失败 | **0** | 0（重试前 7 片 429 限流 + 2 片纯静音） | 部分 | 0 |
| SRT 条数 / 字数 | 147 / 2077 | 146 / **2137** | 140 / 2074 | 120 / **2174** |
| 假名幻觉 | **0** | **0** | **8 处** | 0 |
| 针灸术语（内关/太冲/扎） | **全对** | **全对** | 全错 | 太冲→"外冲"错 |
| 单价 | **¥0.5/小时** | ¥0.79/小时（0.00022 元/秒，filetrans 档） | ~免费（L0 档） | ¥0 |
| 10 分钟成本 | **¥0.083** | ¥0.124 | ¥0 | ¥0 |

## 二、结论与性价比

1. **小米 mimo-v2.5-asr 是全场赢家**：比 SenseVoice 快 **90 倍**、比本地 FireRedASR 快 **18 倍**；
   零失败、零幻觉；质量关键项（针灸术语「内关、太冲」「扎内关」）四家唯一全对；
   最便宜（¥0.5/小时，月转 10 小时仅 ¥5）。10 分钟素材 9.4 秒转完，体感"贴着实时"。
2. **qwen-audio-3.0-asr-flash 质量同档优秀**（字数最全、术语全对、0 幻觉），但有明确短板：
   **并发 8 就触发百炼限流**（Throttling.RateQuota，7/88 片 429，必须配退避重试才能跑通），
   且中位延迟 13.8s 是 mimo 的 3.5 倍、单价高 58%。适合作为备选/热词增强场景
   （其 `output.sentence` 返回**词级时间戳**，是未来真·句级时间轴的升级路径）。
3. **本地 FireRedASR**：免费、离线、无限流，综合质量仍在第一梯队；术语略弱、速度被两家新 API 反超。
   价值在断网/隐私/零成本场景，保留不动。
4. **SenseVoice（现云端主力）全面落后**：慢 90 倍、唯一有幻觉、术语全错——
   免费是唯一优势。建议从主力位退役。

## 三、接入形态备忘（若替换/新增云端引擎）

- **mimo**：`POST https://api.xiaomimimo.com/v1/chat/completions`，OpenAI 兼容；
  `messages` 仅含 `{"type":"input_audio","input_audio":{"data":"data:audio/mpeg;base64,..."}}`
  ——**ASR 请求不能带 text 部分**（网关注入提示词，带文本会 400）。响应 `choices[0].message.content`，
  usage 有 `seconds`（按音频时长计费）。
- **qwen**：**DashScope 原生协议**（非 OpenAI 兼容路径）
  `POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`，
  头 `X-DashScope-SSE: disable`，body 为 `input.messages` + **必填** `parameters.format`
  （缺了报 "format is empty"）。音频支持 base64 data URI（≤10MB）。响应 `output.text`
  + `output.sentence`（句/词级时间戳）+ `usage.duration`。**必须带 429 退避重试**。
- 两者都不支持 OpenAI `/v1/audio/transcriptions` 形态，现 `cloud_asr.py` 的 multipart
  上传姿势需为它们各写一个 adapter（或抽象成 provider）。

## 四、建议（待用户拍板）

- 把 mimo-v2.5-asr 接为**云端首选**（新增 provider），qwen 可选做热词/词级时间戳增强，SenseVoice 退役兜底。
- 回退链不变：cloud(mimo) → firered → whisper。
- 本报告不涉代码改动；接入工作量约 1 个 M1 切片（provider 抽象 + 配置 + 测试）。

> Sources: [阿里云百炼模型价格](https://help.aliyun.com/zh/model-studio/model-pricing) ·
> [Qwen-ASR API 参考](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference) ·
> [非实时语音识别 HTTP API](https://www.alibabacloud.com/help/zh/model-studio/fun-asr-flash-recorded-speech-recognition-http-api) ·
> [MiMo API 定价](https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go) ·
> [MiMo 调价公告](https://mimo.mi.com/docs/zh-CN/news/latest/v2.5-price-update)
