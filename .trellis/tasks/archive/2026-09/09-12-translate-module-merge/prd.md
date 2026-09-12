# translate-module-merge - PRD

## Goal

【2026-09-12 需求转向】原计划"合并全文翻译/字幕翻译为统一翻译模块"已完成调查与设计，用户最终决定：
**整个翻译功能删除**（全文翻译 + 字幕翻译 + 双语对照遗留），同时新增产品级硬规则 **AI 总结必须输出简体中文**。

转向原因：翻译模块多处断头路（目标语言无入口、译文不可见、死代码），且用户实际从不使用翻译——素材要的是中文总结，不是外文译文。

## Requirements

1. 删除翻译功能（前后端全链路）：
   - 后端：main.py Step 4/5、ai_processor.translate_subtitles、routers/translate.py（含 /api/translate/langs）、
     SUMMARY_PROMPTS["translate"]、CATEGORY_PROMPTS、TRANSLATE_LANGS、create_job/schema/批量的 do_translate/do_subtitle 字段
   - 前端：全文翻译/字幕翻译开关、doTranslate/doSubtitle 状态、提交字段、restore 回填、translateApi 封装
   - 契约：重新导出 openapi.json
2. 总结必须中文：prompt_builder 新增 CHINESE_OUTPUT_RULE 常量，模块化路径（含分块重建）与旧 SUMMARY_PROMPTS 路径单点注入；
   full_override / edited_prompt（用户完全接管提示词）不受约束——逐字记录类模板需保留原文

## Acceptance Criteria

- [ ] 全仓 grep "translate/翻译" 仅剩：历史任务 files 的展示 label、无关 CSS transform、ctranslate2、models.py 注释
- [ ] 工作台「处理流程」无翻译开关；提交报文无 do_translate/do_subtitle
- [ ] /api/translate/* 路由不再存在；openapi.json 已重新导出
- [ ] 外文素材跑总结，产出为中文（build_modular_prompt 产物含语言硬规则，单测覆盖）
- [ ] full_override 模板行为不变（不含硬规则）
- [ ] 历史任务不受影响：旧 options 里的 do_translate/do_subtitle 键被忽略不报错；旧翻译产物文件仍可从素材下拉下载
- [ ] 后端 pytest 全绿；前端 vitest + lint + build 全绿（verify.py）

## 不做项

- 不删历史任务已生成的翻译文件（磁盘与 DB files 记录保留）
- 不给 chat 追问/打标签/思维导图加中文强制（本次只约束总结）
- 不改 ResultView

## 影响范围

- 前端：settingsStore / HomeAdvancedOptions / HomeView / useJobRestore / utils/api.js
- 后端：main.py、ai_processor.py、prompts.py、prompt_builder.py、routers/{translate,jobs,jobs_batch}.py、
  models/schemas.py、db/job_repo.py、config(.example).py、contracts/openapi.json
- 测试：test_main / test_routers / test_job_repo / test_bugfixes / test_prompt_builder

## 风险与回滚

- 风险等级：M1（跨前后端，方向已用户确认；纯删除 + 单点注入，无迁移）
- 不可逆操作：无
- 回滚方式：逐单元 git revert

## 调查结论备查（原合并方案调查，转向后仍有效的副产品）

- 文本输入实际总会生成合成 srt（downloader.handle_text_input），旧 Step 4 的 `and srt_path` 条件并不拦截文本任务
- /api/translate/bilingual 前端零调用（双语块 09-06 已删）；translateApi.langs 亦无调用方
- 字幕翻译批次失败静默回退原文（违反禁止静默吞错）——随功能一并删除
