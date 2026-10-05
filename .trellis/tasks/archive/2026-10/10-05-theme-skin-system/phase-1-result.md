# Phase 1 Result — 基线固化

## 改动

- **无源码改动**（本阶段只采集基线）。

## 输出

`research/baseline/`
| 文件 | 说明 |
|---|---|
| `tokens-dark.json` | dark 主题下 `:root` 全部 63 个 `--app-*` 计算值（含 `-rgb` 三元组）；超长媒体值（`--app-bg-image`）已替换为 `<omitted:len=…>` 占位后入库 |
| `tokens-light.json` | 同上，light 主题（通过 `data-theme=light` + 移除 `.dark` 复现，与 `themeStore.applyTheme` 完全同构） |
| `home-dark-1440.png` / `home-light-1440.png` / `history-dark-1440.png` | 工作台与历史页截图（1440×900）。**二进制不入库**（`.git/info/exclude` 本地排除） |

## 验证

- 采集环境：现有服务实例 `0.0.0.0:7001`（PID 45140，`D:\path\python.exe`），其 `backend/static/` 与 worktree 同处 `2a4496f` 基线，故基线有效。
- 命令：DevTools MCP `evaluate_script`（枚举 `document.styleSheets` 中所有 `--app-*` 声明 + 内联 style，再取 `getComputedStyle`）
- 断言：**PASS** — dark 63 项 / light 63 项，数量一致；两套均含 `--app-accent**`（采集时用户自定义主色 `#44ddf5`，非默认 `#38BDF8`，属真实状态，予以保留）
- 断言：截图 3 张均生成（1.88–2.05 MB）

## 偏差

1. **DevTools MCP 无法写入 worktree**：MCP 的工作区根是主仓库 `d:/projects/youtube_tool_v2`，worktree 在其外被拒（`Access denied: not within any configured workspace roots`）。处理：先写入主仓库中转目录 `.tmp-baseline/`，再 `Copy-Item` 进 worktree，最后删除中转目录（主仓库已确认无残留）。
2. **截图决策**：3 张 PNG 合计 ~5.9 MB，不适合入库。改用 baseline 目录内的 `.gitignore`（`*.png`）排除，Phase 2 视觉核对后即可丢弃。
   - **踩坑（worktree 的 exclude 陷阱）**：worktree 下 `git rev-parse --git-path info/exclude` 返回的是**主仓库共享**的 `D:/projects/youtube_tool_v2/.git/info/exclude`（`$GIT_COMMON_DIR`），而 `git rev-parse --absolute-git-dir` 返回 worktree 私有 gitdir `.../.git/worktrees/youtube_tool_v2-theme`。按后者写入的规则**git 根本不会读**——表现为"exclude 文件写得没错，但 `git add` 照样把 png 加进暂存区"，`git check-ignore` 也无输出（exit 1）。目录级 `.gitignore` 无此歧义，故改用之。
3. **token 快照瘦身**：原文件 274 KB/份，其中 ~99% 是自定义背景图的 dataURL。已把长度 >200 的值替换为 `<omitted:len=N>` 占位——比对时该变量本就不应参与（媒体内容，非皮肤派生）。
