# 09-13-template-custom-modules

## Goal

模板中心两处升级：①「新增自定义模块」支持加多个且命名可见可用（现状：存进 DB 但
`get_dimensions_with_overrides` 只遍历内置模块，自定义模块永不显示、显示名称不落库，
体感"只能加一个"）；②模块覆盖安全化：覆盖=旧文本自动备份（最初版+上一版）可一键恢复，
另加「一键用默认文本覆盖」。

## Requirements

1. 自定义模块（`prompt_module_overrides` 中 module_key 不在代码内置列表的行）：
   - `display_name` 落库；维度 API 把自定义模块追加进 modules（标 `is_custom`）
   - TemplatesView 树、DimensionSelector 芯片可见可选；拼装经 override 命中天然生效
   - 新建弹窗：名称必填、key 留空自动生成、同维度重名/key 重复拦截；可加多个
2. 覆盖备份（用户已确认形态：安全覆盖+一键备份恢复+一键默认覆盖）：
   - `original_text`（首次自定义文本，不动）/ `previous_text`（上一版，文本变化时轮转）
   - 编辑区「覆盖备份」面板展示两版本，一键恢复（恢复本身也走备份轮转）
   - 「一键用默认文本覆盖」：用代码默认文本覆盖当前自定义并立即保存
   - 覆盖保存全自动，不加确认弹窗

## Acceptance Criteria

- [ ] 同一维度可连续新增 ≥2 个自定义模块，均出现在模板树/DimensionSelector，名称正确显示
- [ ] 自定义模块可被选中参与拼装（prompt-preview 可见其文本），可删除（删除=移除模块）
- [ ] 修改已自定义模块文本保存后，上一版文本可一键恢复；最初版本始终可恢复
- [ ] 「一键用默认文本覆盖」后 custom_text=代码默认文本，旧自定义进备份
- [ ] backend pytest / vitest / lint / build 全绿；openapi.json 重导出

## Notes

- 风险分级 M1（后端 schema+repo+router+prompt_builder / 前端 api+TemplatesView）。
- 迁移走 database.py 既有 ALTER TABLE try/except 模式。
- 「删除覆盖」「清空所有模块覆盖」保持现状（已有确认弹窗），不在本次范围。
