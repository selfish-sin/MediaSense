# HomeView 巨型文件拆分 - Design

## 拆分清单（5 切片，逐个 commit）

| 切片 | 新组件 | 从 HomeView 迁出 | 契约 |
|---|---|---|---|
| 1 | `components/BackendLogPanel.vue` | 系统日志卡（template L1300-1329 + `.backend-log-panel` 等 CSS）+ `useBackendLogs()` 解构 | 无（组件内自持 composable） |
| 2 | `components/HomeAdvancedOptions.vue` | 高级选项卡（template L951-1119 + `.adv-*` CSS + `engineHint`/`speakerDiarizationDesc`/`transcribeLanguageHint` 3 个 computed） | `v-model:outputOptions` + `emit('touch-model')` |
| 3 | `components/BatchResultPanel.vue` | 批量面板（template L896-937 + `.batch-*` CSS） | `props: batchResult`；`emits: refresh/view-job/confirm-preview/cancel-preview` |
| 4 | `components/JobProgressPanel.vue` | 任务进度卡（template L1135-1202 + `.progress-*`/`.job-*`/`.log-panel` CSS）+ `onProgress`/`onLog` socket 监听 + `showJobProgress`/`jobLogAutoScroll` | `expose reveal()`；HomeView `scrollToJobProgress` 改为调用它 |
| 5 | `components/HomeTokenPanel.vue` | Token 统计卡（template L1359-1439 + `.token-*` CSS）+ token 加载逻辑 | `expose refresh()`；`onAiDone`/`onActivated` 处调用 |

## 关键约束

- `<style scoped>`：CSS 随组件走（scoped 隔离；`:deep` 用法照搬）
- keep-alive：HomeView 被缓存；子组件在 v-if 下挂载/卸载，socket 监听随组件 onMounted/onUnmounted 生命周期，与 HomeView 原语义一致（原 `socketUnsubs` 也是 onUnmounted 清理）
- 行数目标：HomeView ~2100 → ≤1300
- 每切片完成后：`npm run lint` + `npm test` + `npx vite build` + commit（COMMIT-DISCIPLINE）

## 回滚形态

每切片独立 commit；`git revert <hash>` 单点回退。构建产物与源码同 commit 序列提交。
