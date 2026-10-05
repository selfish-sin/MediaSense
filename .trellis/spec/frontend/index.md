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

### 复制与格式化（2026-09-17 固化）

- **复制唯一入口** `utils/clipboard.js` 的 `copyText()`（成功/失败都给 ElMessage）。任何新的复制按钮都调它，
  不要再各写一份 `navigator.clipboard` 逻辑。
- **可测的格式化一律抽纯函数模块**（`utils/chatExport.js` 是范例：`buildAiText` / `buildConversationMarkdown` /
  `parseChatTime`），组件只负责取数据 + 调 `copyText()`。写在 `.vue` 里就没法单测。
- **时间戳坑点**：后端 SQLite `CURRENT_TIMESTAMP` 落库为 **UTC 的 "YYYY-MM-DD HH:MM:SS"（无时区标记）**，
  `new Date()` 会按本地时区解析，东八区会整整差 8 小时 → 必须补 `Z` 按 UTC 读（`parseChatTime`）。
- **v-html 注入的 DOM 不接 Vue 事件**：markdown-it 渲染出的代码块「复制」按钮靠
  `utils/codeCopy.js` 在 document 上挂**一次**委托（`data-md-code-copy`）。
  禁止在某个组件里只给自己接一份——漏接的页面按钮就是死按钮（本项目已为"假按钮"踩过坑）。
- `el-tooltip` / `el-popover` 包裹**内部带 `v-if`** 的元素时，条件为假会触发
  `[ElOnlyChild] no valid child node found` 警告 → `v-if` 要提到 tooltip 外层。

### 主题 / 皮肤 / 效果（2026-10-05 固化）

- **`--app-*` token 名与语义冻结**：21 个组件直接 `var(--app-*)` 消费，改 token 名＝全站回归。新增 token 只增不改；确需改语义时先加别名过渡。
- **皮肤是纯数据**：`utils/skins/` 下每套皮肤只声明 `palette`（约 18 个原始色）或 `tokens`（全量覆盖），其余由 `utils/skin.js` 的 `DERIVE_RULES` 表驱动派生。**新增皮肤＝加数据、不改组件**；新增 token 时在 `DERIVE_RULES` 加一行即可让全部皮肤自动支持。
- **唯一写入点**：只有 `themeStore.applyVisuals()` 写 `:root` 内联样式（优先级高于样式表，故皮肤能覆盖 `App.vue` 的 `:root[data-theme=...]` 兜底）。顺序＝皮肤 token → data-theme → 效果变量 → accent（**accent 最后**，用户主色优先）。
- **效果层与皮肤层职责分离**：配色归皮肤，氛围（遮罩/暗角/玻璃/透明度）归 `utils/effects.js`；效果参数独立存 `mediasense_effects`，与背景媒体解耦。
- **遮罩必须用中性黑/白**（`--app-scrim-rgb`）——带色相的遮罩会把任何背景图统一染成同一种脏色（重构前"背景发灰蒙"的根因）。
- **玻璃卡片必须比背景亮**：dark 模式靠 `--app-glass-sheen`（极淡白高光层）+ 高光描边 + 常态投影（`--app-card-rest`）才浮得起来；深色半透明卡片在深色背景上等于隐形。
- **dark / light 是两套独立材质**（不能简单反转）：light 需要更高的不透明度（surface 基准 +12%）、更深的投影，且描边用主题深色而非白色高光。
- **皮肤导入一律走 `utils/skinSchema.normalizeSkin()`**：失败必须给可行动原因（禁止静默失败、禁止半途应用半套皮肤）；归一化只保留白名单字段，防止外部文件夹带数据。

### 背景媒体（图库 / 动态 / 降级，2026-10-05 固化）

- **图片一律存 IndexedDB 的 Blob**（`utils/bgStore.js`），localStorage 只放 `{type:'image', assetId}` + 几 KB 缩略图。再出现"把 dataURL 写进 localStorage"就是回退到已踩过的坑（配额 ~5MB、base64 膨胀 1.37 倍、deep watch 卡主线程）。
- **写操作必须等事务提交**：IndexedDB 的写要在 `transaction.oncomplete` 才 resolve —— 在 request 回调里 `db.close()` 可能在 commit 前就关掉连接。读操作在 request 成功时 resolve 即可。
- **迁移必须 write-then-clean 且幂等**：先确认写入成功、再清理旧字段；失败保留旧数据继续渲染并给可见提示。
- **objectURL 只有一个创建点与一个 revoke 点**（都在 `themeStore`）；图库缩略图用 dataURL，不要给每张图都建 objectURL。
- **降级路径不删**：IndexedDB 不可用（隐私模式）时整体退回 localStorage 单图路径，并提示一次。
- **动态背景只动 CSS**（`background-position` 漂移）：不跑 `requestAnimationFrame`、不动 `transform`（`transform` 已被 `body::before` 的 scale 占用，用于遮 blur 边）；失焦切 `data-bg-paused`，`prefers-reduced-motion: reduce` 直接关掉。
- **`imageFit` 与 `color.js` 共用一次采样**：不要再写第二份降采样/中位切分；分析失败返回 `null`，不要用 0 值假装成功。
- 测试注意：涉及 Blob 存取的用例要加 `// @vitest-environment node`（jsdom 的 Blob 与 `structuredClone` 不兼容，克隆后不再是 Blob 实例）。

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
