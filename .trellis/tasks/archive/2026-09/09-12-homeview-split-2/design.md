# HomeView 输入区/结果区拆分 - Design

## 拆分清单（2 切片，逐个 commit）

| 切片 | 新组件 | 从 HomeView 迁出 | 契约 |
|---|---|---|---|
| A | `components/HomeInputPanel.vue` | 输入卡 template（输入区）+ 上传逻辑（`handleFileUpload`/`handleFileRemove`/`uploading`/`uploadingFileName`/`uploadRef`）+ `.input-*`/`.upload-*` CSS | v-model ×6：inputType / inputUrl / inputText / batchUrls / uploadedFilePath / uploadedFileName；prop: batchResult；emits: refresh / view-job / confirm-preview / cancel-preview（BatchResultPanel 事件转发） |
| B | `components/HomeResultPanel.vue` | 结果卡 template + 结果区逻辑（编辑/重总结/导出/下载素材/思维导图/流式渲染/复制/思考过程）+ `.result-card`/`.markdown-body`/`.thinking-*` CSS | 无 props；expose `reset()`（handleSubmit 提交时复位 editMode/resultTab）；组件内直接用 jobStore/settingsStore |

## 关键约束

- `<style scoped>`：CSS 随组件走；`.input-main`（输入控件 + 预览卡共用）提升到 App.vue 全局「工作台卡片通用件」区（同 slice-0 模式）
- 持久化 `saveInputState`/`loadInputState` 留在 HomeView（状态仍由 HomeView 持有，供 handleSubmit/useJobRestore 使用）
- HomeView 保留：提交按钮行、转录预览卡、socket 监听（ai token/thinking/done/preview）、通知 watch、批量提交/批量预览确认/取消
- `handleSubmit` 中结果区复位改调 `resultPanelRef.value?.reset()`（语义不变：`editMode=false` + `resultTab='markdown'`）
- `handleResummarize` 的滚动目标 `#result-section` 随组件迁为组件内 root 滚动
- 行数目标：1159 → ≤800
- 每切片完成后：`npm run lint` + `npm test` + `npx vite build` + commit（COMMIT-DISCIPLINE）
- 构建产物（backend/static）随源码同 commit 提交（项目约定）

## 回滚形态

每切片独立 commit；`git revert <hash>` 单点回退。构建产物与源码同 commit 序列提交。
