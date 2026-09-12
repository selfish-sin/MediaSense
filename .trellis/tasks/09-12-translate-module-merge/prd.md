# translate-module-merge - PRD

## Goal

前端「全文翻译」「字幕翻译」两个开关语义重叠、文案不清，且翻译模块存在多处断头路。合并为一个「翻译」开关 + 子选项（目标语言 / 翻译范围），并修复后端静默缺陷。

## Requirements

- 单开关「翻译」+ 子选项：目标语言（9 种，默认简体中文）+ 翻译范围（阅读文稿 / 字幕文件，默认全勾）
- 提交映射：勾文稿→do_translate，勾字幕→do_subtitle；目标语言走 options.translate_target_lang（subtitle_target_lang 保留兼容）
- 修复：全文翻译不再强制要求 srt；翻译输入优先 txt_clean；字幕翻译批次失败可见化
- 译文可见：结果区「译文」tab + 读取端点
- 清理：删 /api/translate/bilingual 死端点与前端封装

## Acceptance Criteria

- [ ] 处理流程只剩一个「翻译」开关，子选项按上述默认值工作
- [ ] 文本输入任务开翻译能出译文（不再被 srt 条件挡住）
- [ ] 全文翻译产物无时间戳（txt_clean 优先）
- [ ] 字幕翻译批次失败时完成日志写明 "N/M 批失败保留原文"
- [ ] 任务完成后结果区可切「译文」tab 直接读译文
- [ ] 后端 pytest 新增用例全绿；verify.py 全量通过

## 不做项

- 不做"任务完成后按需翻译"按钮模式（方案 B）
- 不给 subtitle_category 加 UI 入口
- ResultView（历史页）不接译文 tab，仅工作台结果区
- 不改翻译提示词本身

## 影响范围

- 前端：settingsStore / HomeAdvancedOptions / HomeView / useJobRestore / utils/api.js
- 后端：main.py（Step4/5）、ai_processor.translate_subtitles、routers/translate.py、routers/jobs.py（译文读取端点）
- 历史任务兼容：旧 job 的 do_translate/do_subtitle 布尔回填为新开关+范围

## 风险与回滚

- 风险等级：M1（跨前后端，方案已经用户确认）
- 不可逆操作：无（bilingual 端点无调用方，删除可 git revert）
- 回滚方式：逐单元 git revert
