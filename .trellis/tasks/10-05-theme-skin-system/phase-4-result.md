# Phase 4 Result — 皮肤包（palette 派生 + 4 套 Catppuccin + 导入导出）

## 改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `frontend/src/utils/skin.js` | 改 | 新增 **`DERIVE_RULES` 表驱动派生**（56 条规则）+ `deriveFromPalette()`；`composeTokens` 改为「palette 派生 → tokens 覆盖」两段合成；`resolveSkin(skinId, extra)` 支持用户皮肤；`allTokenNames(userSkins)` |
| `frontend/src/utils/skins/catppuccin.js` | 新增 | 4 个 flavor 的官方调色板 + `makeSkin()`，各只声明 **18 个原始色** |
| `frontend/src/utils/skins/index.js` | 改 | 注册 Catppuccin 四套（`BUILTIN_SKINS` 6 套） |
| `frontend/src/utils/skinSchema.js` | 新增 | `normalizeSkin()` 校验/归一化 + `skinToJson()` 导出；`REQUIRED_PALETTE_KEYS` 单一事实源 |
| `frontend/src/stores/themeStore.js` | 改 | 用户皮肤存储（`mediasense_user_skins`）+ `importSkin`/`removeUserSkin`/`exportSkinJson`/`availableSkins`；**换肤即换主色**（皮肤 `accent` 优先） |
| `frontend/src/components/BackgroundPanel.vue` | 改 | 新增「皮肤」区：6 张色块预览卡 + 导入/导出/删除 + 结果提示条 |
| `src/__tests__/skin.test.js` | 改 | 33 用例（新增 palette 派生、Catppuccin、用户皮肤解析） |
| `src/__tests__/skinSchema.test.js` | 新增 | 19 用例（合法/归一化白名单/11 类非法输入/往返） |

## 输出

- 皮肤数量：**6 套**（legacy 深/浅 + Catppuccin Mocha/Macchiato/Frappé/Latte）
- 新增皮肤成本：**只写 18 个色 + 3 行样板**，其余 50+ token 由派生规则产出
- 皮肤可导出为 `.json`、可再导入；导入失败给可行动原因

## 验证

### 1. 自动门禁
- `npm run test`：**PASS** — 19 文件 / **195 用例**全绿
- `npm run lint`：**PASS**

### 2. 皮肤选择器渲染（真实抽屉路径）
- 点击侧栏「背景」→ 抽屉打开 → 皮肤区显示 **6 张卡片**，各带 4 色预览；当前皮肤有选中态 → **PASS**

### 3. 换肤链路（palette 派生模式）
点击 Catppuccin Mocha 后实测计算值：

| 项 | 实测 | 期望（palette 源） |
|---|---|---|
| `mediasense_skin` | `catppuccin-mocha` | — |
| `mediasense_accent` | `#cba6f7` | 皮肤 `accent`（mauve） |
| `data-theme` | `dark` | `mode` |
| `--app-bg` | `#11111b` | `crust` ✅ |
| `--app-card-bg` | `#1e1e2e` | `base` ✅ |
| `--app-text` | `#cdd6f4` | `text` ✅ |
| `--app-border` | `#313244` | `surface0` ✅ |
| `--app-accent-1` | `#d6b7fa` | mauve 经 `deriveAccentPair` 提亮 ✅ |
| 内联变量数 | **76** | 派生 56 + 效果 13 + 主色 6 ✅ |

→ **换肤即整套换色（含主色）、组件零改动** → **PASS**

### 4. 明暗分支与效果层联动
点击 Latte 后：`skin=catppuccin-latte`、`data-theme=light`、`accent=#8839ef`、`--app-bg=#dce0e8`(crust)、
`--app-scrim-rgb=255, 255, 255`（中性白）、`--app-surface-1-opacity=0.840`（light 材质补偿自动生效）
→ **PASS**（皮肤 → 明暗分支 → 效果层材质全链路联动）

### 5. 导入导出（四条路径，全部实测）
| 场景 | 结果 |
|---|---|
| 导入合法皮肤（`.json`，palette 模式） | 提示「已导入并应用皮肤「我的纸感皮肤」」；卡片 6→**7**；`mediasense_skin=my-paper`；已持久化 → **PASS** |
| 导入缺色皮肤 | 提示「导入失败：palette 缺少或格式错误的必需色：mantle、surface0、…（共需 16 个）」；**卡片数不变、当前皮肤不变、存储未污染** → **PASS** |
| 导入与内置同 id | 提示「id "catppuccin-mocha" 与内置皮肤冲突，请修改文件中的 id 后重试」→ **PASS** |
| 删除用户皮肤 | 提示「已删除该皮肤并回退默认皮肤」；卡片 7→**6**；`mediasense_skin` 回 `legacy-dark`；存储清空 → **PASS** |

### 6. 用户皮肤解析
`resolveSkin('my-skin', [custom])` 命中；不传 extra 时回退默认（单测覆盖）→ **PASS**

## 偏差

1. **用户皮肤存 localStorage 而非 IndexedDB**：设计文档把皮肤存储放在 IndexedDB `skins` store（属 Phase 5/子任务）。皮肤数据只有几 KB，localStorage 完全够用，故本阶段用 `mediasense_user_skins`，避免为几 KB 引入异步层。**待子任务统一图库时再评估是否迁移**。
2. **换肤即换主色**（设计文档未明确）：皮肤自带 `accent` 在切换时覆盖当前主色——这符合"换皮肤 = 换整套配色"的直觉（否则切到 Mocha 主色仍是青色，配色割裂）。用户随后可在面板手动改色。
3. **`allTokenNames` 断言放宽**：原测试断言 `toHaveLength(59)`，现为「至少覆盖 legacy 全部 token 且无重复」——因为 palette 派生规则名与 legacy tokens 名有并集关系，硬编码长度会随皮肤增加而脆弱。
4. **导出按钮会真实触发浏览器下载**：UI 测试只验证点击无报错；导出内容的正确性由 `skinToJson` 往返单测覆盖（导出→再导入结果 `toEqual` 原皮肤）。
5. **Catppuccin 主色选 mauve 而非 blue**：Catppuccin 官方对多数应用推荐 mauve/pink 作强调色，且与本站默认青蓝有区分度；若观感不佳，改 `catppuccin.js` 的 `accent` 一行即可。
