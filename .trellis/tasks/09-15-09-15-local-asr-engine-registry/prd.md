# 本地 ASR 新增通道（引擎注册表）+ Fun-ASR Nano / Qwen3-ASR - PRD

## 收尾结论（2026-09-15，用户决策）

**①保留、②撤回、③保留**：

- ① 引擎注册表（新增通道）**已交付**：本地档 `LOCAL_ENGINES` 单一事实源 + 按 key 通用的
  生命周期与端点；当前只注册 `firered`。
- ② 两个新引擎**评估后放弃**（不是没做完，是实测不达标）：实机横评 RTF 0.5~1.0（FireRed 0.27）
  且成段重复循环幻觉；模型目录与 LLM 解码管线代码已删除。详见
  `research/engine-eval-report.md` 与 spec §5.1。用户原话："用不了算了删掉就行"。
- ③ 收费标识与去「档」文案**已交付**：后端单点 `fee`（云端 provider 收费/免费、本地引擎恒免费），
  前端全面改「收费/免费」，本地页扁平列出引擎列表。

**范围调整**：工作台引擎下拉「动态列出本地引擎」一項**降级为不做**——它原本的目的是暴露两个新引擎，
新引擎撤回后下拉已完整覆盖现存四态（cloud/cloud_free/firered/local），动态化只增加工作台侧风险、
无用户可见收益；`/api/asr/status.local_engines` 端点仍保留（未来加引擎时前端可直接消费）。

## 需求

①本地 ASR 目前是硬编码两家（Whisper 四档 + FireRedASR），**接第三个引擎要改多处代码**——需要一条
「新增通道」：加一个引擎 = 加一条注册表声明 + 一个解码适配函数。
②评估并接入 **Fun-ASR Nano** 与 **Qwen3-ASR（1.7B）**。
③界面/概念**不要「档」**：本地引擎列表扁平化，每条直接标 **免费**；云端 provider 直接标 **免费/收费**
（取代「付费档/免费档」）。

## 调研结论（2026-09-15，Exa 取证）

| 项 | Fun-ASR Nano | Qwen3-ASR |
|---|---|---|
| 本机 sherpa-onnx 1.13.8 支持 | ✅ `from_funasr_nano` | ✅ `from_qwen3_asr` |
| 官方预导出 ONNX | ✅ int8 tar 948MB | ✅ 仅 **0.6B** int8 tar ≈937MB |
| 1.7B 本地 | —（Nano 即 800M，无更大档） | ⚠️ **无官方打包**：仅第三方（ModelScope `zengshuishui/Qwen3-ASR-onnx/model_1.7B` fp32；HF fp16 ≈4.5GB 作者标注面向 CUDA） |
| 语言 | 中文（7 方言/26 口音）+ 英 + 日；歌词/说唱 | 52 语言/方言；含歌词 |
| 依赖 | 无需 torch（onnxruntime） | 同左 |
| 已知坑 | tokenizer 目录名是 `Qwen3-0.6B`；`user_prompt` 默认「语音转写：」 | 输出带 `language <asr_text>` 前缀须裁掉；`max_total_len`/`max_new_tokens` 默认值会**静默截断长音频** |

**结论**：两者都能走「sherpa-onnx + ONNX + 无 torch」通道（与本项目 FireRed 同构）。
Qwen3-ASR 自动下载只做**官方 0.6B**；1.7B 走「手动放置 + 目录可配」通道（无官方包、4.5GB、面向 GPU，
不值得做无校验的第三方自动下载）。

## 具体例子

- 给「本地 ASR」页：列出 4 个本地引擎（Whisper / FireRedASR / Fun-ASR Nano / Qwen3-ASR），每条标注
  **免费**、语言、体积、是否就绪；缺模型的引擎点「下载」即后台拉取并可看进度；已就绪的可删除。
- 给工作台：引擎下拉能选到新引擎（仅在模型就绪或用户显式选择时）。

## 验收标准

- [x] 新增本地引擎只需在注册表加一条声明（+ 解码函数），就绪判定/占用统计/文件明细/下载/取消/删除/占用互斥全部自动生效
- [x] FireRedASR 与 Whisper 现有行为、API 契约、回退链语义**不变**（旧 job options 的 `firered`/`local` 照旧可用；本地端点改通配后 `/local/firered/*` 天然命中）
- [x] `/api/asr/overview` 的本地段改为**引擎列表**（每项含 key/名称/免费标识/语言/说明/就绪/占用/文件明细/下载态）
- [~] ~~新引擎可被工作台选中并真实跑通转录~~ → 已跑通但不达标，**引擎与代码已删**（见收尾结论②；实测数据在 `research/engine-eval-report.md`）
- [~] ~~Fun-ASR Nano 与 Qwen3-ASR 的输出前缀/参数截断坑~~ → 代码已删，教训（tokenizer 传目录、长段跑飞）已写入 spec §5.1 与坑点
- [x] 云端 provider 与本地引擎的收费标识统一为「免费 / 收费」，界面不再出现「档」
- [x] 实测报告（同素材对比的 RTF/耗时/文本质量 + 放弃理由）写入 task 工件 `research/engine-eval-report.md`
- [x] `verify.py` 全绿（pytest 726 / vitest 83 / lint / build）+ 7003 隔离实例浏览器实测（云端·本地两段渲染正确、零 console 错误）

## 不做项

- 不做 Qwen3-ASR 1.7B 的自动下载（无官方包，第三方 fp32 无校验；只提供手动放置 + 目录环境变量）
- 不把新引擎加入**自动**回退链的云端降级路径（云端失败不擅自切到 950MB 本地大模型）；也不加入自动推荐
- 不做 Whisper 在线下载（沿用「手动放置」）
- 不改云端 provider 的协议机制（chat/multipart 两姿势不变）

## 影响范围

- 后端：新增 `services/local_engines.py`（注册表 + 通用生命周期）；`services/local_asr.py`（改为消费注册表、
  保留 FireRed 解码与既有 API）；新增 `services/local_asr_llm.py`（两个新引擎解码）；`services/transcriber.py`
  （引擎白名单/回退链/标签）；`routers/asr.py`、`routers/asr_admin.py`（本地端点按引擎 key 通用化）；
  `main.py`（引擎白名单）；`config.example.py`（两个模型目录）
- 前端：`components/models/AsrLocalPanel.vue`（引擎列表化）、`AsrCloudPanel.vue`（免费/收费 文案）、
  `HomeAdvancedOptions.vue`（引擎下拉按就绪态列出本地引擎）、`stores/asrStore.js`
- 契约：`contracts/openapi.json` 重新导出

## 风险与回滚

- 风险等级：M2
- 不可逆操作：下载/删除本地模型文件（磁盘）
- 回滚方式：按切片 revert；注册表新引擎为纯增量，旧代码忽略即可；FireRed/Whisper 行为保持不变，
  `local` 与 `firered` 引擎值语义冻结
