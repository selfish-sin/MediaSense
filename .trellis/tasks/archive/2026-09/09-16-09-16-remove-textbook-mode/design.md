# remove-textbook-mode - Design

## 架构
删除教科书专用处理管线及其全部入口与配置，不引入新代码。

删除点（后端 5 处 + 文档 1 处）：

1. 删除文件 `backend/services/textbook_processor.py`
2. `backend/main.py`：删 import `textbook_process`、删 `is_textbook` 计算、删 textbook 路由分支（`is_research` 与普通 `ai_process` 分支保持原样）
3. `backend/services/prompt_builder.py`：
   - `PROMPT_DIMENSIONS["depth"]["modules"]` 删除 `textbook` 模块
   - `RECIPES` 删除 `textbook_mode` 配方（8 → 7 个）
4. `backend/config.py` + `backend/config.example.py`：`SUMMARY_TYPES` 删除 `textbook` 条目
5. `backend/tests/test_remaining_services.py`：删 textbook import、删遗留孤儿方法 `test_normal`（历史编辑残留，因缩进被 `TestParseChatResponse` 吸附）、更新模块 docstring
6. `CONTEXT.md`：配方清单、文件索引、当前状态同步

## 契约
无 API 变更（端点集合不变）。`is_textbook` 仅是 `process_job` 内部路由分支，不对外暴露。
`contracts/openapi.json` 无需重导出。

## 数据模型
无 schema 变更。历史 `jobs.options.dimensions.depth='textbook'` 作为惰性数据保留：
`_get_module_template` 对未知 module_key 返回 `""`（不抛错），故此类任务重跑/重总结安全降级为普通总结。

## 已知约束（删除后不影响正确性）
- `test_prompt_builder.py::test_each_recipe_has_dimensions` 断言每个配方 9 个维度、`test_recipe_dimensions_valid` 断言配方引用的模块必须存在 —— 删除是"配方与模块成对删除"，两个断言仍成立。
- 前端 `DimensionSelector` / `SplitPromptPanel` 完全由后端返回的 recipes/dimensions 渲染，删后端即同步消失，无需前端改动。

## 回滚形态
`git revert` 单个 commit 完整恢复（无数据迁移、无外部副作用）。

## 协作模式
后端先行（纯后端删除，前端零改动）。
