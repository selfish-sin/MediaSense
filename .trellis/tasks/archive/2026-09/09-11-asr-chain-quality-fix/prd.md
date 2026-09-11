# asr-chain-quality-fix - PRD

## 需求
在 `09-11-cloud-asr-sensevoice`（云端 SenseVoice 已落地）基础上，修掉转录调用链已核实的体验/韧性缺陷，并清理失效语义：
1. **云端 SRT 句级化**：当前云端 SRT 的「段」=静音分片，一个 3~30s 分片整坨文本塞进一条字幕，与本地 Whisper 句级体验明显退化。写盘前按句末标点切句 + 按字数比例分配分片时长，**不增加任何 API 调用**。
2. **失效选项可见化**：云端引擎静默忽略 `language`（转录语言选择器在云端下仍显示且可改，用户改了没效果）；`speaker_diarization` 同样被忽略。要么隐藏、要么置灰并给明确文案。
3. **删除 `auto` 死语义**：前端只有 `cloud`/`local` 两个选项，`auto` 仅是后端默认值，行为与 `cloud` 几乎一致（差异仅未配置时是否告警）。二选一取「删 auto」，统一为 `cloud`/`local`。

## 背景事实（已核实）
- 云端 API（SiliconFlow SenseVoiceSmall）响应只有 `{"text": "..."}`，**无任何时间戳**，句级时间轴只能靠「分片边界 + 文本比例」近似——这是物理约束，换实现也解不了。
- 前端 `settingsStore.js` 默认 `transcribe_engine: 'cloud'`；`HomeView.vue` 下拉仅 `cloud`/`local`，未配置时云端项 disabled。
- `auto` 引用点：`main.py:773-775`（默认值 + 非法值归一）、`main.py:448,469`（函数默认参）、`transcriber.py:606,610,616,636,819`。

## 具体例子
给一段 2 分钟中文音频 → 引擎=云端 → 当前产出 SRT 约 8 条（每条约 15s 一大坨）→ 修复后产出约 40~60 条句级字幕，时间轴连续单调，API 调用数不变。

## 验收标准
- [ ] `split_sentences(text)` / `distribute_times(start, end, sentences)` 为纯函数，单测覆盖：中英标点断句、短句合并、超长无标点强制断开、比例分配首尾对齐且单调
- [ ] 云端产出 SRT 每条 = 一句（或合并后的短句组），而非整片一坨；`seg_count` 与 SRT 条目数一致
- [ ] 无标点文本（如「文本一」）退化行为不变：1 片 = 1 条，现有编排测试不回归
- [ ] `auto` 从后端选项/校验/默认值中移除，非法值与缺省一律按 `cloud`；前端注释同步
- [ ] 云端引擎下「转录语言」不再让用户做无效选择（隐藏或置灰 + 文案）
- [ ] 后端 pytest、前端 vitest、vite build 全绿

## 不做项
- ~~不接 FireRedASR（小红书）~~ **已废止**：该条基于用户被误转述的原话，用户本人已纠正为「本地部分首选小红书模型」。追加范围见 design.md（M2，用户 2026-09-11 确认）：新增第三引擎 firered + 模型按需下载 + BV1bH4y1r75q 云端 vs 本地对比。
- 不改分片粒度/并发/超时（外部服务特性，`handoff.md` §4 已论证勿动）
- 不做 P0-2 分片级断点续跑（另立切片，本任务不含）
- 不做静音片跳过上传（另立切片，本任务不含）
- 不改 B站字幕时长校验阈值

## 影响范围
- 后端：`services/cloud_asr.py`（句级切分）、`services/transcriber.py`（auto 清理）、`main.py`（engine 校验/默认值）
- 前端：`stores/settingsStore.js`（注释/默认值）、`views/HomeView.vue`（云端下语言选项可见性）
- 测试：`tests/test_cloud_asr.py`（新增纯函数 + 编排断言）

## 风险与回滚
- 风险等级：M1（跨模块，方案清晰，无 schema、无新依赖、无新端点）
- 不可逆操作：无
- 回滚方式：`git revert` 单 commit；句级切分是纯本地后处理，删掉即恢复「一片一条」
