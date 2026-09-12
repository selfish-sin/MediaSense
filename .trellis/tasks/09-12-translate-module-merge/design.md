# translate-module-merge - Design

## 架构

单一事实源收敛：前端一个「翻译」开关 + 范围勾选，映射回后端现有 do_translate/do_subtitle 布尔（后端流程 Step4/5 结构不变，零迁移）。语言参数统一为 options.translate_target_lang，subtitle_target_lang 仅作历史兼容回退读取。

```
前端 settingsStore:
  doTranslate(bool, 主开关) + translateScope(['text','srt'], 勾选) + advancedOptions.translate_target_lang
提交: do_translate = doTranslate && scope含'text'
      do_subtitle  = doTranslate && scope含'srt'
后端 Step4: 条件 do_translate and txt_path（去掉 and srt_path）
      输入: files.txt_clean 存在则读之，否则 txt_path
      语言: options.translate_target_lang（默认 zh-CN）
后端 Step5: 语言 = options.subtitle_target_lang or options.translate_target_lang（默认 zh-CN）
      translate_subtitles 失败批次计数 → 完成日志 "N/M 批失败保留原文"
译文读取: GET /api/jobs/{id}/translation → 读 files.translation 文件内容
```

## 契约

- `GET /api/translate/langs` 保留（前端子选项语言下拉数据源）
- `GET /api/jobs/{id}/translation` 新增：200 `{translation, target_lang}`；404 任务不存在；400 无译文
- `POST /api/translate/bilingual` 删除（无调用方）
- 请求侧零 schema 变化：do_translate/do_subtitle 布尔语义不变，options.translate_target_lang 原本就存在（前端从未传过）

## 数据模型

无表结构变化。历史任务回填（useJobRestore）：doTranslate = do_translate || do_subtitle；scope['text'] = do_translate(缺省 true)；scope['srt'] = do_subtitle(缺省 true)。

## 回滚形态

三个单元各自独立 commit（后端修复 / 前端合并 / 译文 tab），任一出问题单独 revert。

## 协作模式

后端先行（前端映射依赖后端参数语义稳定）。
