# 真实内容质量评测（eval/）

自动回归验证程序行为，验证不了「中医术语识别得对不对、剂量有没有丢、摘要漏不漏重点」。
这一层要靠**真实样本**评测：同一份讲座音频/文本，跑不同引擎/模型，横向对比。

工具：`backend/scripts/eval_quality.py`（指标纯函数在同目录 `eval_metrics.py`）。

## 目录

- `samples/*.json` — 样本清单（**入库**；音频/参考文本放本机，路径写进清单）
- `reports/` — 评测报告与原始结果（**不入库**，已在 .gitignore）

## 样本清单格式

见 `samples/tcm.example.json`。字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 样本名（报告分组用） |
| `audio` | 转录评测需要 | 音频/视频文件路径（本地文件，勿入库） |
| `transcript` | 建议 | 人工核对过的参考文本——有它才能算 CER；也是摘要评测的输入 |
| `terms` | 建议 | 术语清单（穴位/方剂/药名），逐个查命中 |
| `dosages` | 建议 | 剂量片段（如 `黄芪30g`），检查转录与摘要是否保留 |
| `language` | 可省 | 转录语言，默认 `zh` |
| `summary_dimensions` | 可省 | 摘要提示词维度覆盖（如 `{"role":"tcm"}`） |

## 样本准备建议

- 片段 5–15 分钟：足够暴露术语/剂量问题，CER 和成本都可控。
- 参考文本：人工听写或以校正后文本为基础**人工复核**（直接拿机器输出当参考会偏向该机器）。
- 术语/剂量清单：从参考文本里挑容易错的（同音字、生僻穴名、含数字的剂量）。
- 中医讲座用 `summary_dimensions: {"role":"tcm"}` 贴近真实使用。

## 运行

```powershell
cd backend
./venv/Scripts/python.exe scripts/eval_quality.py `
    --samples ../eval/samples/tcm.json `
    --engines firered,cloud_free `
    --models glm-5-2 `
    --out ../eval/reports/run1
```

- `--engines`：`firered` / `local`（Whisper，配 `--model-size`）/ `cloud_free` / `cloud`。
  **`cloud` 是付费档（MiMo），只有显式列入才会被调用。**
- `--models`：摘要模型 key（模型管理页里的 key）。评测钉死单模型，不走兜底环。
- 默认**全新识别**（绕过转录/AI 缓存），`--use-cache` 才允许复用。
- 输出：`report.md`（人读）+ `results.json`（原始数据，含每次引擎实际落点/降级/耗时/token）。

## 指标口径

| 指标 | 含义 | 方向 |
|---|---|---|
| CER | 字错误率近似 = 1 − 归一化相似度（difflib），无参考文本时为「无参考」 | 越低越好 |
| 术语 | 归一化子串命中（`terms_hit`），报告列缺失明细 | 越高越好 |
| 剂量 | 同上，针对剂量片段 | 越高越好 |
| 无出处数字 | 摘要出现、转录原文没有的「数字+单位」——幻觉/编造剂量代理 | 越少越好 |
| 耗时 / tokens | 每引擎/模型墙钟时间与 token 消耗（成本参考） | 越低越好 |

## 判读提示

- 云端引擎有突发限流（历史上 MiMo 免费档 429 打掉过 38/125 片）；单次结果波动大时跑 2–3 个样本再下结论。
- 引擎「降级」列会标出实际落点（如请求 firered 但模型未就绪落到 local）——降级结果不能计入该引擎的成绩。
- 摘要质量除了指标，建议人工抽查 `results.json` 里每模型的 `summary_text` 全文——
  指标抓不住「整句语义替换」「口径漂移」这类问题（历史教训：校正器曾整句重写原文）。
