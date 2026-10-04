# Phase 2 Result — 皮肤 token 层等价重构（**零视觉变化**）

## 改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/src/utils/skins/index.js` | 新增 | 内置皮肤数据（`legacy-dark` / `legacy-light`，各 59 个 token，值与重构前 `App.vue` 的 `:root[data-theme]` 块逐值相同） |
| `frontend/src/utils/skin.js` | 新增 | 合成/注入/解析逻辑（`resolveSkin` / `skinMode` / `composeTokens` / `skinForMode` / `writeTokens` / `removeTokens` / `allTokenNames`），纯函数 |
| `frontend/src/stores/themeStore.js` | 改写 | `theme` 由独立可写 ref 改为**由皮肤派生的 computed**；新增 `skin` / `skinId` / `setSkin`；初始化走 `applySkin()`（皮肤 tokens → data-theme → accent 顺序） |
| `frontend/src/__tests__/skin.test.js` | 新增 | 21 个用例 |

**关键手法**：皮肤 token 写入 `:root` **内联样式**（优先级高于样式表），legacy 两套的值与旧样式表值相同 → 观感不变，同时皮肤层从此成为唯一值来源。

## 输出

- 3 个源码文件 + 1 个测试文件（见上）
- 产物：`backend/static/` 已 build 验证通过，但**未提交**（留到 Phase 5 与其他阶段一起刷新，避免中间态产物噪音进历史）

## 验证

### 1. 单元测试
- 命令：`cd frontend; npm run test`
- 结果：**PASS** — 17 文件 / **139 用例**全绿（含新增 `skin.test.js` 21 个）

### 2. 零视觉变化（闸门核心）— 发布态逐值比对
- 命令：`npm run build` → `vite preview:4173` → DevTools 采 `getComputedStyle` 全部 `--app-*`
- 对照：Phase 1 基线 `research/baseline/tokens-{dark,light}.json`
- 前提：把 5174/4173 的 `localStorage` 对齐基线状态（`mediasense_accent=#44ddf5` + 相同 background 参数 + 清空 `mediasense_skin`）
- 排除项（11 个，不参与比对）：`--app-bg-image`（媒体内容）+ `--app-accent*` 6 个（accent 直出，输入值不同）+ `--app-chip-active-bg`/`--app-chip-active-soft`/`--app-progress-meter-bg`/`--app-thinking-bg` 4 个（引用 accent）

| 主题 | 可比项 | 字符串完全一致 | 差异项 |
|---|---|---|---|
| dark | 52 | 49 | 3 |
| light | 52 | 46 | 6 |

- 差异项**全部为同色不同写法**（样式表取值被 Chrome 序列化 vs 内联取值原样返回）：
  - `rgba(0,0,0,0.42)` ≡ `#0000006b`（0.42×255=107.1→`0x6b`）
  - `#ff9900` ≡ `#f90`
  - `rgba(15,23,38,0.86)` ≡ `#0f1726db`（219.3→219）
  - `rgba(11,17,32,0.92)` ≡ `#0b1120eb`（234.6→235）
  - `rgba(15,23,42,0.08)` ≡ `#0f172a14`（20.4→20）
  - `rgba(255,255,255,0.9)` ≡ `#ffffffe6`（229.5→230）
  - `#ffffff` ≡ `#fff`
- 用**浏览器自身**（canvas `fillStyle` 规范化）把两侧值归一到同一表示再比较 → **8 组全部 `equal: true`**（dark 4 组 + light 4 组）。断言：**PASS**

### 3. 明暗切换可用（真实交互路径）
- 点击侧栏主题按钮（真实 DOM 点击，非直接改属性）→ 断言：**PASS**
  - `skinId` → `legacy-light` 并持久化到 `mediasense_skin`
  - `data-theme=light`、`.dark` class 移除、内联变量 63 个、`--app-bg` 变 `#f0f4fa`
  - 按钮 title 自动变为「切换到夜间模式」（说明 `theme.theme` 响应式仍工作）

### 4. 组件零侵入（命题验证）
- 命令：`git status --short -- frontend/src`
- 断言：**PASS** — 改动仅 `stores/themeStore.js` + 3 个新文件，**0 个 `.vue` 组件改动**。App.vue / BackgroundPanel.vue 对 `theme.theme` / `theme.toggle()` 的既有用法原样可用。

### 5. 界面健康与 console
- DOM：`#app` 存在、`.app-sidebar` 存在、6 张卡片、5 个菜单项
- 计算值：`body` bg=`rgb(240,244,250)`、color=`rgb(31,42,60)`（与 light token 一致）
- `list_console_messages`（error/warn 过滤）：**无消息**
- 断言：**PASS**（无白屏、无错位、无报错）

## 偏差

1. **测试文件后缀**：设计文档写 `.spec.js`，实际项目约定为 `.test.js`（`src/__tests__/*.test.js`）。跟随项目约定，未另立门径。
2. **palette 派生规则未在本阶段实现**：`composeTokens()` 目前只做 tokens 全量覆盖，派生规则表推迟到 Phase 4（首套 palette 模式皮肤 Catppuccin 落地时一并引入）。理由：本阶段目标是最小风险的等价重构，提前写无消费者的派生逻辑会降低可验证性。
3. **像素级 diff 不可行，改用更严格手段**：基线背景图是用户私有图片（dataURL 存于 7001 origin 的 localStorage），跨 origin 无法复现同一画面，故无法做像素 diff。改用「token 逐值比对 + 浏览器颜色规范化等价」——变量全等且 CSS 规则未改，则可断言渲染一致。这是比截图像素比对更强的证据（精确到 token 而非像素抽样）。
4. **踩坑：dev server 端口漂移**。本次启动的 vite dev server 因用户已有实例占用 5173 而自动改用 **5174**；首次采集误连到 5173（用户的实例，跑主仓库旧代码），表现为"内联变量只有 10 个、`data-theme` 恒为 light、存在旧键 `mediasense_theme`"。**判据：新代码必然写入 63 个内联变量**，以此识别实例是否为目标代码。
5. **踩坑：worktree 缺 gitignored 文件**。`backend/config.py` 未入库（gitignored），worktree 内 `uvicorn main:socket_app` 因此报 `ModuleNotFoundError: No module named 'config'`。改用 `vite preview` 服务 build 产物完成发布态验证（主题为纯前端，无需后端）。
6. **构建产物处置**：Phase 2 的 `npm run build` 结果已用 `git checkout -- backend/static` + `git clean -fd backend/static`（先 dry-run 确认 24 个文件全为本次 build 生成的哈希产物）回退，产物刷新统一在 Phase 5 进行。
7. **本阶段未提交的既有问题**：build 输出含 `@vueuse/core` 的 `INVALID_ANNOTATION` 警告（rolldown 对 `/* #__PURE__ */` 位置提示），来自 node_modules 而非本项目代码，与本任务无关。
