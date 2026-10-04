# Phase 5 Result — 验收与知识回写

## 1. 全量门禁（L 级 verify）

| 项 | 命令 | 结果 |
|---|---|---|
| 后端 pytest | `backend/venv/Scripts/python.exe -m pytest tests/`（cwd=backend） | **837 passed**, 3 warnings |
| 前端 lint | `npm run lint` | **PASS**（无输出） |
| 前端 vitest | `npm test`（经 `scripts/run-vitest.mjs`） | **20 文件 / 221 用例全绿** |
| 前端 build | `npx vite build` | **PASS**（1.87s，产物已刷新到 `backend/static/`） |

> 后端源码本任务**零改动**（`git status --short -- backend` 为空）；worktree 无 `backend/venv`，故借主仓库 venv 的 python 在 worktree 代码上执行。

## 2. 验收标准逐条核对（PRD A1–A9）

| # | 标准 | 判定 | 证据 |
|---|---|---|---|
| A1 | 所有页面 × 6 套皮肤 × 明暗 无不可读文字/错位/颜色残留 | **达成（含已知局限）** | ①新增 `themeContrast.test.js`（26 用例）对**全部 6 套皮肤**断言：正文 vs 卡片底 ≥4.5:1、正文 vs 页面底 ≥4.5:1、次要文字 ≥3:1、标题层级不倒置；②首页 6 套皮肤真实点击切换，逐项核对 token 生效；③Mocha / Latte 视觉确认截图。**局限见 §4** |
| A2 | 多层 scrim；浓度 0 不压暗；最大时对比度 ≥4.5:1 | **达成** | Phase 3：`dim/vignette/top/bottom=0` 实测背景完全不被压暗；对比度实测 12.68:1（实底）/ **5.05:1（背后纯白，最坏）** |
| A3 | 玻璃三件套 + 3 档 surface 可辨 | **达成** | `blur(14px) saturate(170%)` + 高光描边；surface 0.72/0.80/0.88（light 0.84/0.92/1.00），实测计算值 |
| A4 | 皮肤为纯数据、面板可切换、可导出导入 | **达成** | 6 套皮肤（`utils/skins/`）；4 条导入导出路径实测（成功 / 缺色 / id 冲突 / ~~删除~~ 已移除） |
| A5 | 背景图可存多张、不受 localStorage 配额限制、旧背景可升级 | **移出本任务** | 用户当轮决定：IndexedDB 图库拆为独立子任务（本任务据此从 L 降为 M1） |
| A6 | 至少 1 种程序化动态背景 + 失焦暂停 | **移出本任务** | 同上（依赖 A5 的背景媒体层） |
| A7 | 按图片亮度自动推荐遮罩强度 | **移出本任务** | 同上 |
| A8 | lint / test / build 全绿；后端不受影响 | **达成** | §1 全量门禁；后端 837 全绿且源码零改动 |
| A9 | 无静默失败（存储/解码/皮肤非法均可见） | **达成** | 皮肤导入失败给可行动原因（缺色逐项列出、id 冲突给修改建议）；`themeStore.persist` 配额溢出经 `storageError` 显示；`@supports not` 与 `prefers-reduced-transparency` 降级；`deriveFromPalette` 单条规则异常 `console.warn` 且不阻断整皮肤 |

## 3. 知识回写

| 文件 | 改动 |
|---|---|
| `CONTEXT.md` | ①修正 `backend/verify.py` → **`verify.py`（仓库根）**；②`App.vue` 行改写为"主题兜底 + 背景/卡片材质层"；③**新增 6 行文件索引**（`themeStore.js` / `skin.js` / `skins/` / `effects.js` / `skinSchema.js` / `BackgroundPanel.vue`），每行含"何时读"；④当前状态新增本任务条目（含 7 条坑点） |
| `.trellis/spec/frontend/index.md` | 新增「主题 / 皮肤 / 效果（2026-10-05 固化）」小节：token 冻结约定、皮肤纯数据、唯一写入点、效果层分离、遮罩必须中性黑白、玻璃必须比背景亮、dark/light 双材质、导入必须校验 |
| `.trellis/project-foundation.json` | ①`tokenSource` 修正（原指向**已删除**的 `frontend/src/style.css`）→ App.vue 变量 + `skin.js` + `effects.js`；②`verifyCommand`/`verifyFullCommand` → `python verify.py`；③`lastUpdated` → 2026-10-05 |

## 4. 静态检查发现的皮肤覆盖度边界（**已知局限，需向后传递**）

对 `frontend/src/**/*.vue` 扫描硬编码色值（`#rgb` / `rgba(数字…)`）后发现 20 个文件存在硬编码色，性质分三类：

| 类型 | 例子 | 判断 |
|---|---|---|
| 代码块专用配色 | `ChatPanel.vue` 的 `#1e1e1e` / `#e6e6e6` / `#333` | **有意独立**（代码块在浅色主题下也保持深色，属常见设计） |
| 状态/图标色 | `#34d399` / `#fbbf24` / `#60a5fa` | 可接受（语义状态色，各皮肤差异不大） |
| **白色叠加层** | `ChatPanel.vue` 的 `rgba(255,255,255,0.07|0.1|0.25|0.8)` | ⚠️ **真隐患**：在 light 主题下白色叠加几乎不可见，某些分隔/描边会"消失" |

- 这三类**均非本任务引入**（重构前就在），且 legacy 皮肤与重构前逐值等价，故原观感不受影响。
- 换肤到 Catppuccin 后，未 token 化的硬编码不会跟随 → **皮肤覆盖度 = token 化程度**，这是本次架构的固有边界。
- 不在本任务修（会波及 20 个组件、超出"皮肤/背景"范围），**记录为后续可选项**：若要提升皮肤一致性，需把这些色收敛为 token。

## 5. 遗留（明确移出，需另立任务）

- **背景媒体层子任务**（原 A5/A6/A7）：IndexedDB 图库（存 Blob、迁移旧 dataURL）、动态背景（Canvas/CSS + 失焦暂停）、按图片亮度自动推荐遮罩参数。
  → 设计文档 `design.md` §6 已给出完整方案（存储结构、`write-then-clean` 迁移链、`imageFit` 规则），可直接作为该子任务的输入。
- 面板与 `skinSchema` 已为图库预留 `type: 'image'` 分支（`themeStore.applyBackground` 注释标注）。

## 6. 偏差汇总（跨阶段）

1. Phase 2 的"逐值等价"仅对其 commit 成立；Phase 3/4 是有意变更（light 材质补偿、卡片常态投影、玻璃高光层、`emphasis` 方向修正）。
2. 测试文件后缀 `.test.js`（设计文档写 `.spec.js`）——跟随项目既有约定。
3. 用户皮肤存 localStorage（设计文档原定 IndexedDB `skins` store）——数据仅几 KB，避免为小数据引入异步层。
4. 「删除用户皮肤」功能按用户要求移除（`ceb21b5`）。
5. **`emphasis` 方向修正**（本阶段由新门禁发现）：`--app-text-heading`/`--app-log-time`/`--app-code-text`/`--app-thinking-text` 原统一"提亮"，在 light 主题下会**降低对比度**；改为按 mode 翻转（dark 提亮 / light 加深）。
