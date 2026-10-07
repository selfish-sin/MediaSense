# quality-eval-harness - PRD

## 需求
自动回归验证不了「中医术语对不对、剂量丢没丢、摘要漏不漏重点」。
建立真实样本评测框架，用中医讲座等样本横向对比转录引擎与摘要模型，形成选型依据。

## 交付
- `backend/scripts/eval_quality.py`：评测编排 CLI（转录引擎横评 + 摘要模型横评）
- `backend/scripts/eval_metrics.py`：指标纯函数（CER 近似/术语/剂量/无出处数字）
- `eval/README.md` + `eval/samples/tcm.example.json`：样本格式与操作指南
- 指标单测 21 条

## 边界（外部调用范围由用户决定）
- 付费档 cloud（MiMo）只在显式列入 --engines 时调用
- 默认绕过缓存全新识别；--use-cache 才复用
- 真实跑批需要用户提供：样本音频+人工参考文本+术语/剂量清单
- 本框架不自动跑任何真实素材

## 验收
- [x] 指标纯函数单测 21 条通过
- [x] 脚本 --help/样本加载/文本清洗实测可用
- [x] verify 全绿
