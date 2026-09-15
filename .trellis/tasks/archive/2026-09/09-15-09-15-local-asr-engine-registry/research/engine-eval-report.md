# 本地 LLM 解码类引擎横评报告（2026-09-15，实测后放弃）

> 结论先行：**两条路线都不采用**。Fun-ASR-Nano-2512(int8) 与 Qwen3-ASR(ONNX) 已接入并跑通，
> 但在 CPU int8 上既慢又不可靠（重复循环幻觉），模型目录与相关代码已删除，只保留引擎注册表基础设施。
> 复现口径：`backend/services/local_asr.py` 的 `transcribe_audio_local_llm`（已删除）+ 注册表声明。

## 1. 素材与方法

- 素材：`齐泽克——资本(研讨会：直至世界尽头) … 中配 p01 配音.m4a`，ffmpeg 截 `-ss 300 -t 90`
  → 16k 单声道 wav（90.0s / 2813 KB），确保两引擎输入**逐字节相同**。
- 机器：CPU（无 GPU），sherpa-onnx 1.13.8，`provider='cpu'`；FireRed 用仓库既有甜点参数
  （threads=3 / 6 workers），LLM 引擎用 `LOCAL_LLM_DECODE_WORKERS`（默认 2）。
- 指标：段数 = 句级化后的 SRT 条数；墙钟 = 含模型加载的总耗时；RTF = 墙钟 / 音频时长；
  另记函数内 `耗时` 行（纯解码段）。
- 环境隔离：bench 脚本通过 `YT_TEST_DATA_DIR` 指向临时目录，产物不落真实 `data/`。

## 2. 首轮结果（各自默认参数）

| | 段数 | 墙钟 | RTF | 内测耗时 | 文本字数 | 质量 |
|---|---|---|---|---|---|---|
| firered（AED int8） | 18 | 23.9s | 0.265 | 18s（4.9x） | **686** | 标点自然、语义连贯、无幻觉 |
| funasr_nano（800M int8） | 68 | 46.1s | 0.512 | 36s（2.5x） | 3265 | `interest in the world you're a little bit of a little bit of a lit…` 重复循环、出现 0 秒段 |

- 首轮 funasr_nano 其实**先报错没跑起来**：`Cannot find tokenizer.json in: .../Qwen3-0.6B/tokenizer.json`
  —— 注册表的 part 解析出的是 **tokenizer.json 文件**（便于就绪判定），而 sherpa 工厂要的是
  **tokenizer 目录**。传 `Path(paths['tokenizer']).parent` 后才正常解码（该 bug 已记入教训）。

## 3. 调参复测（试图救回 Fun-ASR Nano，全部无效）

| 改动 | 结果 |
|---|---|
| VAD 段上限 20s→12s | 段数 68→130、字数 3265→6444、RTF 0.512→**1.014**（更碎片 → 自回归调用更多 → 更慢），幻觉仍在（`those are the ones that are the ones…`） |
| 显式 `language='zh'` | 仍是中英混出，无改善 |
| `max_total_len/max_new_tokens` 放大（2048/512） | 无改善（首字重复、人名被听成英文串如 `jamesonexmason`） |
| `tokenizer` 传目录（真 bug 修复） | 从"跑不起来"变成"能跑但结果不可用" |

同轮对照（同素材同参数）：firered 18 段 / 18s / 686 字，文本与首轮逐字一致。

## 4. 结论与决策

1. **CPU int8 下 LLM 解码类 ASR 不适合本项目**：RTF 0.5~1.0（FireRed 0.27，慢 2~4 倍），
   且出现成段重复循环幻觉——幻觉内容会直接被写进 SRT/TXT 并进入下游摘要，风险远大于收益。
2. **放弃接入**：`funasr_nano` / `qwen3_asr` 两条注册表声明、LLM 解码管线（`_build_llm_recognizer`/
   `_decode_llm_segments`/`transcribe_audio_local_llm`/文本清洗函数）、引擎值/回退链/标签、
   相关单测全部删除；已下载模型目录（funasr_nano 971MB、qwen3_asr 553MB 未下完）一并删除，释放约 1.5GB。
3. **保留的收益**：引擎注册表（加引擎 = 一条声明 + 一个解码函数）、按 key 通用的本地生命周期
   端点 `/api/asr/local/{engine}/*`、overview 本地段引擎列表、收费标识后端单点——这些与引擎数量无关，
   是本次任务留下的真实价值。
4. **若将来换 GPU 重试**：先按本报告口径（同素材、同参数、比对 RTF + 字数 + 幻觉检查）复测，
   达标后再谈接入；`Qwen3-ASR` 官方无 1.7B 打包（1.7B 走第三方导出）也是当时的减分项。

## 5. 教训（已进 spec 坑点）

- `tokenizer` 参数语义按工厂而异：sherpa 的 LLM 类工厂要**目录**，注册表解析出的是**文件**。
- 长段是自回归解码跑飞的主因之一，但缩短段长会放大调用次数与总耗时——不是免费午餐。
- 模型文件删除的测试必须隔离目录：本次流程中出现过「用例真的删掉 `models/` 下 FireRedASR ≈1.5GB」
  的事故（已按字节还原），修复见 `tests/conftest.py::_isolate_model_dirs` 与 spec 坑点 15。
