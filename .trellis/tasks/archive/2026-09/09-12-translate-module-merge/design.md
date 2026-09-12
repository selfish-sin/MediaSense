# translate-module-merge - Design

【2026-09-12 需求转向定稿】原"合并翻译模块"设计作废，改为：删除翻译功能 + 总结强制中文。

## 架构

### 删除清单（后端 → 前端，按依赖序）

```
main.py: Step 4 全文翻译块 + Step 5 字幕翻译块 + translate_router 注册
ai_processor.py: translate_subtitles() + CATEGORY_PROMPTS import
prompts.py: SUMMARY_PROMPTS["translate"] 条目 + CATEGORY_PROMPTS 段
routers/translate.py: 整文件删除（/api/translate/langs 一并无调用方）
models/schemas.py: JobCreate.do_translate / do_subtitle 字段
routers/jobs.py / jobs_batch.py: do_translate/do_subtitle 读取与 create_job 传参
db/job_repo.py: create_job(do_translate/do_subtitle) 参数与 options 写入
config(.example).py: TRANSLATE_LANGS + SUMMARY_TYPES["translate"]
前端: HomeAdvancedOptions 两开关、settingsStore.doTranslate/doSubtitle、
      HomeView 提交字段、useJobRestore 回填、api.js translateApi
```

### 历史兼容

- 旧 job options 里的 do_translate/do_subtitle 键：提交侧不再读取，存储侧原样保留（inert）
- 旧任务已生成的 `__zh_CN.md`/`__zh_CN.srt`：磁盘与 files 记录保留，素材下拉 label 保留（翻译字幕/翻译文本）
- useJobRestore 继续从 restoredOptions delete 旧键，防泄漏进 advancedOptions

### 总结输出语言（最终实现：可选维度，非注入机制）

用户裁定用选择栏替代最初的 CHINESE_OUTPUT_RULE 注入机制（机制版实现一次后撤回）：

```
prompts.LANGUAGE_DESCS = {简体中文(默认), 繁体中文, 英语, 跟随素材}
prompt_builder.PROMPT_DIMENSIONS + "language" 维度（前端选择器自动渲染）
build_modular_prompt 选项块在字数后追加「输出语言：{desc}。」
历史任务 dimensions 缺 language 键 → get_default_dimensions 兜底简体中文
未知语言值 → 语言行优雅省略；full_override 完全接管提示词，无选项块
前端 DIMENSION_KEYS 加 language；matchRecipeKey 只比对配方已定义的维度
```

不变量：语言是用户可选的输出属性而非强制规则；默认值满足"总结必须中文"。

## 契约

- 删除 `POST /api/translate/bilingual`（本任务前半程已删）、`GET /api/translate/langs`（本次删）
- JobCreate 请求体移除 do_translate/do_subtitle（多余字段本就被 pydantic 忽略，无破坏性）
- `contracts/openapi.json` 重新导出（export_openapi.py）

## 数据模型

无表结构变化。

## 回滚形态

三个单元独立 commit（后端删除 / 中文规则 / 前端删除），任一出问题单独 revert。

## 协作模式

后端先行。
