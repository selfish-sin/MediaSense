# Frontend Spec（Vue3 + Element Plus + Pinia）

## Pre-Development Checklist

改任何 frontend 代码前确认：

- [ ] 读过 `CONTEXT.md` 文件索引表
- [ ] 构建产物输出到 `backend/static/`（同源 SPA），改完源码需 rebuild 才会反映到后端托管页面
- [ ] 涉及 Socket.IO 时读过 `utils/socket.js`——`onXxx` 辅助函数返回解绑函数，组件卸载时必须逐个调用

## 核心约定

### 组件纪律

- 视图文件超过 ~800 行考虑拆分：逻辑块抽 composable（`src/composables/`）、UI 块抽子组件
- socket 监听解绑函数收进数组，`onUnmounted`/`onDeactivated` 统一清理，禁止跨组件残留监听
- 定时器（轮询）同样必须在 `onDeactivated`/`onUnmounted` 回收；HomeView 被 `keep-alive` 缓存，恢复逻辑放 `onActivated`

### Composable（2026-08-27 从 HomeView 拆出）

- `useBackendLogs`：日志面板——增量拉取（delta 超界回退全量重载，禁止静默丢弃）、连续失败 ≥3 次才告警一次（成功重置计数）
- `useBatchProgress`：批量进度——默认订阅 Socket.IO `batch_progress` 事件，断线降级 10s 轮询、恢复后停表；新增批量面板字段改 summary 结构即可
- `useJobRestore`：路由参数回填（`?rerun=jobId` 等），依赖通过参数注入（route/router/store 等），不直接 import
- 新 composable 必须先补 `src/__tests__/` 测试再迁移视图代码；mock 用注入/stub，不引真实后端

### 状态与 API

- 跨视图共享状态进 Pinia store（jobStore/modelStore/settingsStore/themeStore/asrStore），视图私有状态留在视图
- axios 拦截器（`utils/api.js`）统一把后端 `detail` 转成 `Error(msg)`；调用方必须 catch 并给用户可见反馈（ElMessage），禁止空 `catch {}`
- 四态覆盖：loading（按钮禁用/v-loading）、empty（空态提示）、error（可见可行动）、success
- store 失败语义分两类（`asrStore` 是范例）：**只读总览**失败落 `error` 字段并清空列表（供页面失败态，
  不展示陈旧数据），**写操作**抛错由视图 ElMessage 提示；可写 `computed` 做设置项，保存失败时显示值
  自动回到后端真值（避免"UI 已改、后端没改"的假成功）
- Element Plus 交互坑：`el-select` 必须点 `.el-select__wrapper`（点外层 `.el-select` 事件不冒泡到触发器，
  下拉不会打开）；判断 popper 可见性用 `getComputedStyle(popper).display`，**不要用 `offsetParent`**
  （fixed 定位元素恒为 null）；`el-dropdown` 的菜单项会为每行都渲染并全部留在 DOM 里，
  按文案 find 会命中第一行的菜单——必须先按可见性过滤

### 构建与路由

- Vue Router history 模式，SPA fallback 由后端 `serve_vue_spa` 处理
- 深层页面刷新依赖后端路由兜底返回 index.html（勿改后端 SPA 路由的 no-cache 语义）

## Quality Check

- [ ] `cd frontend && npm run test`（vitest）全绿
- [ ] `cd frontend && npm run build` 通过；若需后端生效，产物已输出到 `backend/static/`
- [ ] 新交互无静默失败路径（catch 后用户有感知）
- [ ] 离开页面后无残留定时器/socket 监听（DevTools console 无报错）
