# remove-textbook-mode - PRD

## 需求
删除「教科书模式」功能。用户确认不再使用（2026-09-16 实测该模式单任务耗时 12~30 分钟，且存在 token 统计恒为 0、>99 分钟视频各小节内容雷同、重新总结绕过教材管线等缺陷，优化性价比不足）。

## 具体例子
- 输入：工作台打开「维度预设」→ 期望：配方卡片中不再出现「📖 教科书模式」
- 输入：「深度」维度展开 → 期望：模块列表中不再出现「教科书模式」
- 输入：对历史 `options.dimensions.depth='textbook'` 的任务点「重新总结」→ 期望：走普通总结、不报错

## 验收标准
- [ ] `backend/services/textbook_processor.py` 已删除
- [ ] 代码与 config 中不再有 `textbook` 残留（历史文档/归档任务中的记述不算）
- [ ] `main.py` 无 `is_textbook` 分支，`is_research` 与普通 `ai_process` 分支行为不变
- [ ] `prompt_builder` 的 `RECIPES` 与 `PROMPT_DIMENSIONS["depth"]` 不再含 textbook 模块
- [ ] `config.py` / `config.example.py` 的 `SUMMARY_TYPES` 不再含 textbook 条目
- [ ] 后端 pytest 全绿；前端 lint + vitest + build 全绿
- [ ] 真实入口浏览器验证：工作台配方列表无教科书卡片、深度维度无教科书模块

## 不做项
- 不删除「深度研究」（research）模式
- 不清理历史任务数据（dimensions 里的 textbook 值惰性保留）
- 不新增/删除任何 HTTP 端点

## 影响范围
- 后端：`main.py` 任务路由、`prompt_builder.py` 维度与配方、`config*.py` 摘要类型、测试
- 前端：无源码改动（配方与维度均由后端 `/api/summary/*` 驱动）
- 历史任务：`dimensions.depth='textbook'` 的任务重跑降级为普通总结

## 风险与回滚
- 风险等级：M1（跨模块但方案清晰，无 schema/API 变更）
- 不可逆操作：无
- 回滚方式：`git revert` 对应 commit
