# 本地 ASR 新增通道 - Design

## 最终实现与偏差（2026-09-15 收尾，以本节为准）

| 计划 | 实际 |
|---|---|
| 新建 `services/local_engines.py` 放注册表 | **并入 `services/local_asr.py`**（注册表 + 生命周期 + FireRed 解码同一文件，避免为 1 个引擎拆文件与循环导入） |
| 新建 `services/local_asr_llm.py` 放两个新引擎解码 | **未创建**：两条 LLM 路线实测不达标后删除，LLM 解码管线（构造/清洗/段级解码/入口）随之删除 |
| `LOCAL_ENGINES = {funasr_nano, qwen3_asr, firered}` | **`LOCAL_ENGINES = {firered}`**；Whisper 仍不在此表（独立模型族，端点单列） |
| 引擎值/回退链/标签扩两档 | **未扩**（`ENGINE_VALUES` 仍是 cloud/cloud_free/firered/local；`_FALLBACK_CHAINS` 一字未动） |
| 前端工作台引擎下拉按就绪态动态列本地引擎 | **降级不做**（原目的只是暴露两个新引擎；现存四态已完整）。`/api/asr/status.local_engines` 端点保留备用 |
| slice-05 三引擎横评 | **已做且结论为负面**，报告：`research/engine-eval-report.md`（FireRed 18s/RTF 0.27/686 字 vs Nano 36~82s/RTF 0.5~1.0/幻觉） |

已交付且与计划一致：注册表单一事实源与通用生命周期、本地端点 `/local/{engine}/*` 通用化
（旧 firered 路径天然命中）、`/overview` 本地段改引擎列表、收费标识后端单点 + 前端去「档」、
前端本地页改注册表驱动、测试隔离加固（防用例删真实模型）。

## 架构（原计划，保留作历史记录）

```
services/local_engines.py  ← 「新增通道」：声明式引擎注册表 + 通用生命周期
├─ EngineSpec（key/名称/免费标识/语言/说明/主目录/关键文件/下载包/手动放置提示）
├─ LOCAL_ENGINES = {funasr_nano, qwen3_asr, firered}   ← 加引擎只改这里
└─ 通用能力（与具体引擎无关，全部按 key 分发）
   engine_status(key) / all_engines_status()   就绪 + 文件明细 + 占用
   engine_busy(key)                            解码互斥（删前守护）
   start_download(key) / cancel_download() / download_state()   单例下载状态机
   remove_engine(key)                          删除并把下载态复位
        │
services/local_asr.py        ← FireRed 解码（保留）+ 共享 sherpa 辅助（VAD/wav/写盘/标点）
services/local_asr_llm.py    ← 新引擎解码：funasr_nano / qwen3_asr（共用段级解码管线）
        │
services/transcriber.py      ← 引擎白名单 / 回退链 / 显示标签（单点）
routers/asr.py + asr_admin.py← 本地端点按引擎 key 通用化（不再硬编码 fireered 三个端点）
frontend AsrLocalPanel.vue   ← 引擎列表（免费/收费 标签），不再硬编码两张卡
```

依赖方向单向：`transcriber → local_asr_llm → local_asr → local_engines`（无环）。

## 引擎注册表（单一事实源）

```python
EngineSpec(
    key='funasr_nano',                     # 引擎值：写进 job options，一经发布冻结
    name='本地 Fun-ASR Nano',
    languages='中文（7 方言 / 26 口音）/ 英文 / 日文',
    note='通义 Fun-ASR-Nano-2512（800M，int8 约 950MB）；亦支持歌词与说唱识别',
    model_dir=lambda: cfg_path('FUNASR_NANO_MODEL_DIR', models/funasr_nano),
    parts=(('encoder_adaptor', '音频适配器', 'encoder_adaptor.int8.onnx'),
           ('llm', 'LLM 解码器', 'llm.int8.onnx'),
           ('embedding', '嵌入层', 'embedding.int8.onnx'),
           ('tokenizer', '分词器', 'Qwen3-0.6B/tokenizer.json')),
    archives=(Archive(url=<k2-fsa release tar.bz2>, label='Fun-ASR Nano 模型（约 950MB）',
                      kind='tar', dest=lambda: 同 model_dir),),
)
```

- **就绪判定**：`parts` 全部存在且 size>0（缺哪个都判未就绪，禁止「文件在但残缺」）
- **占用统计**：主目录递归字节
- **下载**：按 `archives` 顺序流式下载（tar.bz2 → 解包时把单一顶层目录改名成 `dest`；单文件直接落位），
  进度按包数均分 0~8%（与既有 FireRed 口径一致）；失败文案含手动下载指引
- **手动放置**：`note` + `manual_hint`（目录）在页面展示——Qwen3-ASR 1.7B 走这条路

## 引擎值语义（扩展但不改旧的）

```
_FALLBACK_CHAINS（新增两档；旧的 cloud/cloud_free/firered/local 四条一字不动）
  funasr_nano : funasr_nano → firered* → local
  qwen3_asr   : qwen3_asr   → firered* → local      (* = 模型就绪才参与，兜底绝不触发下载)
```

- **不进云端降级路径**：cloud/cloud_free 的链保持 `…, firered*, local`——云端失败不擅自切到
  950MB 本地大模型（那会变成隐性大下载 + 长时间等待）
- **不参与自动推荐**（`recommended_engine` 不含新引擎，同 firered 规则）
- 用户显式选新引擎 → 失败降级 `firered*` → `local`（Whisper 链尾）
- `_ENGINE_LABELS` 增两条；`main.py` 白名单同步

## 两个新引擎的解码适配（`local_asr_llm.py`）

共用管线（与 FireRed 同构）：ffmpeg 16k 单声道 wav → silero VAD 分段（+0.15s padding，512/块喂）→
段间并行解码 → 复用 `cloud_asr.split_sentences/distribute_times` 句级写盘。

差异（各自 recognizer + 文本清洗）：

| 引擎 | 构造 | 文本清洗 |
|---|---|---|
| funasr_nano | `from_funasr_nano(encoder_adaptor, llm, embedding, tokenizer, num_threads, provider='cpu', max_new_tokens=512, temperature=1e-6, top_p=0.8, seed=42)` | 去首尾空白；`user_prompt` 用默认「语音转写：」 |
| qwen3_asr | `from_qwen3_asr(conv_frontend, encoder, decoder, tokenizer, num_threads, provider='cpu', max_total_len, max_new_tokens, temperature, top_p, seed)` | **必须裁掉 `language <asr_text>` 前缀**；`max_total_len`/`max_new_tokens` 按段长放大（默认 512/128 会静默截断） |

两个清洗函数都是纯函数 → 单测锁定（`_strip_asr_prefix`、`_clean_nano_text`）。

## 契约变化（`/api/asr`）

| Method | Path | 变化 |
|---|---|---|
| GET | `/overview` | `local` 段重构：`{engines: [{key,name,fee,languages,note,ready,busy,bytes,dir,parts:[],download:{...},manual_hint}], whisper:{models,default_model,device}, device}` |
| GET | `/local/{engine}/download` | 通用化（原 `/local/firered/download`），engine ∈ 注册表 |
| POST | `/local/{engine}/download` · `/local/{engine}/cancel` · DELETE `/local/{engine}` | 同上 |
| GET | `/status` | 新增 `local_engines:[{key,name,ready}]` 供工作台引擎下拉按就绪态展示 |

**兼容**：旧路径 `/local/firered/*` 保留为等价别名（前端与外部脚本不受影响）。
`DELETE /local/whisper/{size}` 不变（Whisper 仍是 4 档，不属于「引擎注册表」，属独立模型族）。

## 收费标识

- 云端 provider：`engine=cloud` → **收费**；`engine=cloud_free` → **免费**（去掉「档」字）
- 本地引擎：一律 **免费**
- 单点：后端 `/overview` 返回 `fee` 字段（`'免费' | '收费'`），前端只显示不推断

## 回滚形态

- 代码：按切片 revert
- 数据：无 schema 变更（两个模型目录为纯新增；`job options` 的旧值语义不变）
- 行为：新引擎不进自动链/自动推荐，未下载时对既有流程零影响

## 协作模式

后端先行（注册表 + 解码）→ 实测取证 → 前端（引擎列表 + 文案）→ 串联验收。

## 切片清单

- slice-01 注册表 + 通用生命周期（`local_engines.py`，FireRed 迁移到注册表且行为不变）
- slice-02 两个新引擎解码适配 + 引擎值贯通（transcriber/main 白名单/回退链/标签）
- slice-03 本地端点通用化 + `/overview` 本地段重构（含旧路径别名）
- slice-04 前端：本地引擎列表（免费/收费）+ 云端文案去「档」+ 工作台引擎下拉按就绪态
- slice-05 实测横评（同素材 FIRERED / Fun-ASR Nano / Qwen3-ASR）
