# 背景图库与 IndexedDB 迁移 - PRD

> 来源：从 `10-05-theme-skin-system`（背景与皮肤系统重构）拆出的独立子任务。
> **设计已完成**：可直接继承父任务的 `design.md` §6.2（IndexedDB 存储）与 §6.3（迁移方案），无需重新调研。
> 状态：`planning`（未开工）。父任务 Phase 5 完成后启动本任务。

## 需求

背景图目前以 dataURL 存 localStorage（配额实测仅 ~5MB，4MB 图必 `QuotaExceededError`），只能保存 1 张、无法做图库与轮换。需要迁移到 IndexedDB 存 Blob，支持多图收藏与切换。

## 验收标准

- [ ] B1 背景图可保存 ≥5 张并在面板图库中切换，不受 localStorage 配额限制。
- [ ] B2 存储为 Blob（非 dataURL），无 base64 的 +33% 膨胀。
- [ ] B3 旧格式 `{type:'custom', url:dataURL}` 首次启动自动迁移进 IndexedDB，**write-then-clean**（先写成功再清理旧字段，失败保留旧路径继续渲染）。
- [ ] B4 迁移幂等：重复启动不产生副本（按 assetId 判重）。
- [ ] B5 IndexedDB 不可用（隐私模式/配额拒绝）时退回 localStorage 单图路径，并有用户可见提示。
- [ ] B6 对象 URL 生命周期正确：切换/卸载时 `URL.revokeObjectURL`，无内存泄漏。

## 不做项

- 不做云端同步、不做图库导入导出 ZIP、不做图片编辑。

## 影响范围

- 新增 `frontend/src/utils/bgStore.js` + `__tests__/bgStore.spec.js`
- 改动 `stores/themeStore.js`、`components/BackgroundPanel.vue`（父任务已为其定义好扩展点）
- 依赖父任务 Phase 2 的皮肤层（背景对象结构届时已定型）

## 风险与回滚

- 风险等级：**M2**（含数据迁移；建议开工时按 §1.1 复核）。触发点：不可逆的旧数据清理。
- 回滚：迁移失败一律保留旧字段（write-then-clean），因此可随时退回 localStorage 路径；`bgStore.js` 为新增文件，删除即回滚。
