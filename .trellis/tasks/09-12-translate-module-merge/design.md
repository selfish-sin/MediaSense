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

### 总结中文硬规则（单点定义）

```
prompt_builder.CHINESE_OUTPUT_RULE = "【语言硬规则】无论素材原本是什么语言，本次总结/分析的输出一律使用简体中文；确需引用外文关键术语/原文时，保留原文并在其后用括号附中文释义。"

注入点 1: build_modular_prompt() 末尾追加（覆盖模块化路径 + 分块重建；full_override 分支提前 return 不受约束）
注入点 2: ai_process 旧路径 prompt_template 追加（edited_prompt 编辑态不受约束）
```

不变量：用户完全接管提示词（full_override / edited_prompt）时保留原文语义优先于强制中文。

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
