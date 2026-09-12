# 免费档（XingChen Ultra）同素材实测报告

> 2026-09-12 | 素材：BV1bH4y1r75q 前 10 分钟（与 archive/09-12-asr-vendor-compare 完全同素材）
> 方法：`--download-sections` 只取前 600s（加固姿势）→ `cloud_asr.transcribe_audio_cloud(provider='xingchen')`
> 全文：`free-eval-output.txt`；脚本：`eval_free_asr.py`（研究用，不入运行路径）

## 一、结果

| 指标 | XingChen Ultra（免费） | mimo-v2.5-asr（付费，基线） |
|---|---|---|
| 总耗时（10min 素材） | **21.2s** | 9.4s |
| 条数 | 138 | 147 |
| 字数 | **2109** | 2077 |
| 假名幻觉 | **0** | 0 |
| "内关/太冲"术语 | 出现即正确 | 全对 |
| 同音字错例 | **灵龟八法→零规八法、太冲→再充、内关→内弯** | 未发现（同段正确） |
| 单价 | **限时免费** | ¥0.5/h |

结论：**免费档日常可用**——零幻觉、字数更全、速度 28x 实时（10min 素材 21s，远快于本地
FireRedASR 168.8s / Whisper CPU）。短板是专业术语**同音字错误率高于 mimo**（中医场景实测
"灵龟八法""太冲""内关"各有错例），对通用视频影响小；专业内容建议开「AI 校正」
（glm-5.2 横评最优）或该任务临时切 mimo。

## 二、附带结论：抽查片段下载姿势可用

同一脚本验证了 B站 音频 `--download-sections '*0-600'`（带完整浏览器指纹 UA/buvid/cookie）：

```
[download-sections] OK: clip.m4a 6933 KB, 3.3s
```

3.3s 拿到 6.9MB 前 10 分钟片段（对比整段下载需数十秒~分钟级）。据此将字幕内容抽查
改为"只下前 90s 片段"（`download_video(max_seconds=90)`，失败自动回退完整下载），
长视频有字幕任务的抽查下载成本从"整段"降为"90s"。

## 三、免费优先策略（同任务落地）

`/api/asr/status` 新增 `recommended_engine`：免费档已配置 → `cloud_free`；否则付费档 →
`cloud`；都不可用 → `local`。前端默认引擎据此取值（用户手选不覆盖），"免费使用"成为
默认路径而非手动操作。
