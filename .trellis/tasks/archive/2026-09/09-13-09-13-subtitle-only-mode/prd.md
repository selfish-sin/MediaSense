# 09-13 仅生成字幕模式 - PRD

## 需求
我要解决什么问题：工作台加一个「仅生成字幕」任务模式——只产出字幕文件（SRT/TXT），不做 AI 总结。字幕来源二选一（用户定稿：无字幕时**自动回退转录**，不做严格失败）：
1. 自动（默认）：优先B站/平台字幕，拿不到（或抽查不过）自动回退转录；
2. 直接转录生成：跳过平台字幕，直接转录音频生成字幕。

★ 用户拍板后范围收敛：后端现有链路（`do_summary=false` + `force_transcribe`）已完整覆盖上述语义，
**零后端改动**，纯前端把已有能力包装成显式模式。

## 具体例子
- 给一个有 AI/CC 字幕的B站 URL + 模式=仅生成字幕 + 来源=平台字幕 → 任务直接拿到 SRT/TXT 完成，全程不下载完整音频、不转录、不调 AI 总结。
- 给一个没有字幕的B站 URL + 同上配置 → 任务失败，错误信息可行动（提示切「转录生成」来源重试），不自动转录。
- 给任意 URL + 模式=仅生成字幕 + 来源=转录生成 → 下载音频 → 按所选引擎转录 → 拿到 SRT/TXT 完成，不总结。
- 给本地音视频文件 + 模式=仅生成字幕 → 转录后拿 SRT/TXT 完成（来源选择不出现）。
- 批量 URL 提交继承同一模式与来源。

## 验收标准
- [ ] 仅生成字幕 + 来源自动：B站有字幕 → completed（SRT/TXT/txt_clean，不总结）；无字幕/抽查不过 → 自动回退转录后完成，仍不总结
- [ ] 仅生成字幕 + 来源直接转录 → 等价 force_transcribe=true 路径，完成后不总结
- [ ] 完整处理模式（默认）行为与现状完全一致
- [ ] 批量提交继承模式与来源（payload 已带 do_summary + options，天然继承）
- [ ] 从历史「到工作台重做」回填模式与来源（do_summary/force_transcribe 回填已有，无需改动）
- [ ] verify.py 全绿（pytest / vitest / lint / build）

## 不做项
- 不改 JobCreate schema、不加新端点（subtitle_only 走 options 自由字段）
- 不做「仅字幕」专用的 SRT 预览/编辑界面（沿用现有转录 tab + 文件下载）
- 不给字幕内容抽查加新开关（沿用 subtitle_check 默认值）
- YouTube 之外的通用平台不做字幕抓取增强（无字幕来源时明确失败）

## 影响范围
★ v1 返工记录：模式选择器曾放输入区，用户否决（输入区只管输入），未 commit 已全部回退；
   v2 方案全部落在 HomeAdvancedOptions + settingsStore + HomeView 提交按钮文案，纯前端：
- frontend/src/stores/settingsStore.js：jobGoal/subtitleSource computed 映射
- frontend/src/components/HomeAdvancedOptions.vue：处理流程区重构（模式卡/提示块/来源子选项/2列网格）
- frontend/src/views/HomeView.vue：提交按钮文案联动
- frontend/src/__tests__/settingsStore.test.js：映射单测
- 后端 / 批量 / restore：零改动（do_summary + force_transcribe 既有字段直读直传）

## 风险与回滚
- 风险等级：M1（跨前后端，方案清晰，无 schema/API 变化）
- 不可逆操作：无（options 为自由 JSON 字段，无迁移）
- 回滚方式：git revert 单提交序列即可；旧任务 options 无 subtitle_only 键，默认行为不变
