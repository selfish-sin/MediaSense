# Phase 3 Result — 效果层（scrim 多层 + 玻璃三件套 + surface 分档）

> 本阶段是**有意的视觉变更**（不再要求与重构前等价）。

## 改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/src/utils/effects.js` | 新增 | `DEFAULT_EFFECTS` / `effectVars()` / `loadEffects()`——把视觉参数换算为 CSS 变量（纯函数） |
| `frontend/src/stores/themeStore.js` | 改 | 新增 `effects` 独立状态（`mediasense_effects` 键）+ `setEffect`/`resetEffects`；`applyVisuals()` 统一应用皮肤+效果+主色；背景媒体与效果参数解耦 |
| `frontend/src/App.vue` | 改 | `body::after` 单色平涂 → **四层 scrim**；`.app-card/.wb-card` → 玻璃三件套 + 高光层 + 常态投影 + `@supports`/`prefers-reduced-transparency` 降级；`.el-card` 同步；`.el-dialog`/`.el-drawer` → surface-3/2 |
| `frontend/src/components/BackgroundPanel.vue` | 改 | 滑块重排为「氛围遮罩」5 项 + 「卡片材质」2 项，全部绑定效果层 |
| `frontend/src/utils/skins/index.js` | 改 | `legacy-light` 的 `--app-card-glow` 加深（0.08→0.16） |
| `frontend/src/__tests__/effects.test.js` | 新增 | 24 个用例 |

## 输出

- 效果变量（写入 `:root` 内联）：`--app-scrim-{rgb,dim-a,top-a,bottom-a,vignette-a}`、`--app-glass-{blur,saturate,border,sheen}`、`--app-surface-{1,2,3}-opacity`、`--app-card-rest`、`--el-card-box-shadow`、`--app-card-opacity`（兼容别名）、`--app-bg-blur`
- 背景设置面板：7 个滑块（整体浓度 / 径向暗角 / 顶部渐晕 / 底部渐晕 / 背景模糊 / 卡片透明度 / 玻璃模糊）

## 验证

### 1. 自动门禁
- `npm run test`：**PASS** — 18 文件 / **164 用例**全绿（新增 effects 24 个）
- `npm run lint`：**PASS**（无输出）

### 2. 效果变量逐项生效（DevTools 实测计算值）
```
scrimRgb=0, 0, 0        dimA=0.182      vignetteA=0.358    （dark）
glassBlur=14px          glassSat=170%   sheen=rgba(255,255,255,0.045)
surface1/2/3 = 0.720 / 0.800 / 0.880                     （dark，基准 72%）
surface1/2/3 = 0.840 / 0.920 / 1.000                     （light，基准 72% + 12% 材质补偿）
cardBg=rgba(11,17,32,0.72)  cardBgImage=linear-gradient(sheen)  backdrop=blur(14px) saturate(1.7)
cardBorder=rgba(255,255,255,0.14)   resolvedShadow=rgba(15,23,42,0.1) 0 2px 12px  （light）
body::after = linear-gradient(top) , linear-gradient(to top) , radial-gradient , linear-gradient(dim)
```
- 断言：**PASS**（四层 scrim 已合成、玻璃三件套齐备、surface 三档递进）

### 3. A2 遮罩（两半）
- **浓度为 0 时完全不压暗**：置 `dim/vignette/top/bottom = 0` 后截图，背景彩色块明亮饱和、无边缘压暗，卡片仍清晰 → **PASS**
- **最大时文字对比度 ≥ 4.5:1**：按 WCAG 相对亮度公式计算（页面内实测）
  | 场景 | 对比度 |
  |---|---|
  | 卡片完全不透明（`#d6dee8` on `#111b2e`） | **12.68:1** |
  | 72% 不透明度 + 背后纯白（最坏） | **5.05:1** |
  | 72% + 中灰 `#808080` | 8.77:1 |
  | 72% + 明亮照片色 `#fadca0` | 5.84:1 |

  → 即使最坏情况（背后纯白）仍 **5.05:1 > 4.5:1** → **PASS**

### 4. A3 玻璃与分档
- 三件套：`blur(14px)` + `saturate(170%)` + 高光描边（dark `rgba(255,255,255,.14)` / light 主题深色细边）→ **PASS**
- surface 三档递进（0.72 < 0.80 < 0.88；light 0.84 < 0.92 < 1.00）→ **PASS**
- 数据密集区（`.el-table`）保持实底未改 → **PASS**

### 5. 新旧视觉对照（隔离浏览器上下文 + 同一张高对比测试图）
- before = `localhost:5173`（重构前代码，旧 `dim=22` 单色遮罩、`cardOpacity=72`）
- after = `localhost:5174`（本分支，`dim=26` + 四层 scrim、`cardOpacity=72`）
- 断言：**PASS** — before 卡片几乎透明、背景色块直接干扰正文、无暗角；after 卡片有实体边界与投影、背景退为氛围层、边缘压暗。截图存 `research/baseline/`（本地，按 .gitignore 不入库）

### 6. 降级路径
- `@supports not (backdrop-filter)` → 近实底 0.96；`prefers-reduced-transparency: reduce` → 全实底 + 关 blur（CSS 静态检查 + 规则存在性确认）

## 偏差

1. **新增「玻璃高光层」`--app-glass-sheen`**（设计文档未列）：仅靠半透明深色底 + blur，卡片在深色背景上等于隐形（实测截图确认）。业界做法是让玻璃**比背景亮**，故 dark 模式叠一层 `rgba(255,255,255,0.045)`；light 模式为 `transparent`（浅色卡片本就比背景亮）。
2. **发现并修复真 bug：卡片常态没有投影**。项目原有实现只在 `.app-card:hover` 给 `box-shadow`，常态 `box-shadow` 为空 → 浅色模式下"白卡片压浅色背景"完全无法分层（实测 `resolvedShadow: none`）。新增 `--app-card-rest`，并与 Element Plus 的 `--el-card-box-shadow` 同源，避免两套阴影打架（dark 用纯黑、light 用低透明度冷色）。
3. **light 材质补偿 +12%**：设计文档未预见。dark/light 是两套独立材质（业界共识），浅色主题里浅色卡片缺对比，故 `surfaceBase = card + 0.12`，并将 `legacy-light` 的 `--app-card-glow` 由 0.08 加深到 0.16。
4. **Phase 2 的"逐值等价"不再覆盖本阶段**：上述三项是**有意的视觉变更**。Phase 2 的等价性结论仅对其自身 commit（`9161f2a`）成立，这正是分阶段提交的价值——需要回退观感时 `git revert` 到该 commit 即可。
5. **light 模式在明亮照片上仍偏"奶白雾"**：已通过材质补偿与投影改善，但浅色玻璃压在明亮照片上本质受对比度限制，未达"惊艳"。需用户在实际界面上确认后再决定是否继续调参（此项记录为待用户反馈，不作为本阶段未完成项）。
6. **沿用 Phase 2 的实例识别判据**：dev server 因用户实例占用 5173 而落在 5174；用「写入 63 个内联变量 + `--app-scrim-rgb` 存在」区分新旧代码。
