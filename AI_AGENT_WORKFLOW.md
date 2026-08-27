# AI_AGENT_WORKFLOW.md

> 2026-07-21 | 面向 AI Agent 的可执行工作流规范（v6.4）
> changelog: 2026-08-07 — 新增 spec 双源收敛机制(§7.1)、push 硬门禁(§6 PUSH-GATE)、等级变动问人(§9)、项目形态适配(§4.4)；补 bootstrap 依赖、verify 回退、Windows 路径；接入 Z Code(§11)；强化 COMMIT-DISCIPLINE(§2.1)。
> changelog: 2026-08-07c (v6.0 大改) — M 级拆 M0/M1/M2；删 OpenSpec 残骸；五阶段 GUARD/EXIT 三套块合并单行；§6 加理解层审查；附录瘦身；全文档按等级裁剪去繁琐。
> changelog: 2026-08-07d (v6.1 一致性审查) — 修 13 处前后矛盾/实战不适配：M0/M1 边界模糊、Archive commit 与 COMMIT-DISCIPLINE 冲突、§6.3 断链、记忆冲突判定逻辑、§5/§6 Mock 自相矛盾、M1 前端四态、组件库卡与 bootstrap 冲突、Codex skills 目录、M0 无 prd 恢复、verify 命令假设、Build DevTools 即时验证、审查顺序、契约验证范围。复杂工程高频反模式(§10.5)从附录孤儿段归位并修截断。
> changelog: 2026-08-07e (P1 复核) — 撤销"bootstrap 无模板/无 PowerShell 跑不了"误判：Z Code/OpenCode/CodeBuddy/Claude Code/Codex 均具备命令执行+文件读写能力，无模板时手动建骨架即可。仅修 §11.1 脚本矛盾（throw 硬中断 vs 注释"可手动建骨架"）：模板缺失改提示+跳过，复制步骤加 $sourceProject 保护。
> changelog: 2026-08-07f (v6.2 多 Agent 轮换补强) — 补三处断点：§0.3 跨 Agent 接手对齐（进度只信仓库：git log + task.py current + 基础设施完成度检查，[WIP-FAIL] 停靠识别）；§0.1 STEP 3 加 S 级轻量入口（明确根因小 bug/迭代不进 §3，直接定位→改→验证→commit）；§3 开头声明 S 级不进入 Open。P1 已撤销，P2/P3/P4 全补齐。
> changelog: 2026-08-07g (v6.3 全文档一致性核修) — 修 6 类断链/矛盾：§4.3.1→§4.3；§6.1 第 4 条→§6 第 4 步；4 处 §7.3→§7.1（CONTEXT.md 维护规范实际在 §7.1）；§11.1/§11.6 补 .zcode（与 §11.0/§11.2/§11.4 对齐，bootstrap 后 §11.4 验证不再因缺 .zcode 失败）；编码红线引用 §10.4→§10.1+§2.2（§10.4 是组件库策略）。§0.3 接手对齐基础设施验证前置到第 0 步（先搭基础设施再走流程，无 .trellis 不卡死）。
> changelog: 2026-08-07h (v6.3 追加) — §0.3 接手对齐补分支检查：当前分支 log 为空 → git branch --all + git worktree list（防 worktree 隔离下误判没进度）。漏洞 1/2 已由第 0 步闭环；漏洞 4/5/6 不补（防御违规 agent 无效且费 token）。
> changelog: 2026-08-07i (v6.4 启动线合并) — §0.1/§0.3 两条启动线合并为一条：§0.1 STEP 2 吸收全部接手增量（基础设施验证前置→git 状态未提交收尾→分支检查→WIP-FAIL 识别→task.py current 无条件执行），0-1 步标记为接手强制项；§0.3 精简为一条说明 + 2 条 RULE，执行步骤全部指向 §0.1，不再重复。附录 B 命令正斜杠统一为反斜杠（与正文一致）。§11.1 步骤 12 补防漂移注释（改 §7.1 须同步规则块）。
> 本文档读者是 AI，不是人。每个章节包含：可执行指令、模板骨架、判定条件、字段定义。
> 原则只出现在"为什么这么干"的 1 行注释中，不独立成节。
> 项目环境配置（Agent/模型/券）放各项目 `CONTEXT.md` 或 `ENV.md`，不写进本文。

---

## 目录

- [0. 会话启动协议](#0-会话启动协议)
- [1. 风险分级与流程路由](#1-风险分级与流程路由)
- [2. 四件套调用规则](#2-四件套调用规则)
- [3. Open 阶段（需求澄清）](#3-open-阶段需求澄清)
- [4. Design 阶段（技术方案）](#4-design-阶段技术方案)
- [5. Build 阶段（实现）](#5-build-阶段实现)
- [6. Verify 阶段（验收）](#6-verify-阶段验收)
- [7. Archive 阶段（归档）](#7-archive-阶段归档)
- [8. 临时需求与范围锁](#8-临时需求与范围锁)
- [9. 流程逃逸与时间盒](#9-流程逃逸与时间盒)
- [10. 质量门禁](#10-质量门禁)
- [11. 新建项目接入](#11-新建项目接入)
- [12. 流程健康度](#12-流程健康度)
- [附录 A：文件模板](#附录-a文件模板)
- [附录 B：命令速查](#附录-b命令速查)

---

## 0. 会话启动协议

### 0.1 启动检查清单

会话开始时，按以下顺序执行。每步有明确的通过/不通过判定。

```
STEP 1: 判断任务类型
  IF 纯问答/不修改项目 → 跳到用户问题，不走流程
  IF 需要改动项目 → 继续 STEP 2

STEP 2: 加载项目上下文（按需，不全量；0-1 步为接手强制项，其余按需）
  0. EXEC: §11.4 基础设施验证 → 不过 → 先补完 §11 bootstrap 再继续（含无 .trellis 场景）
  1. READ: AGENTS.md（项目治理块）
  READ: CONTEXT.md（项目架构/技术栈/坑点）
  EXEC: git status（看当前工作树状态）
    IF 有未提交改动 → 先按 §2.1 收尾（完成它并 commit，或标记 [WIP-FAIL]），不留静默半完成
  EXEC: git log --oneline -10（看进度：最近 commit 即"做到哪了"，进度只信仓库，§0.3）
    IF 当前分支 log 空 → EXEC: git branch --all + git worktree list（进度可能在别的分支，切过去再看）
    IF 最近提交含 [WIP-FAIL] → 上个 Agent 在此失败，READ 失败记录/半成品，判断重做或接着修
  EXEC: python .trellis\scripts\task.py current（§11.4 已过，必可运行）
    IF 有 active task → READ: .trellis/tasks/<active-task-dir>/prd.md（M0 无 prd 则跳过）
    IF 有 active task → READ: .trellis/tasks/<active-task-dir>/implement.md
  IF 涉及代码 → READ: .trellis/spec/ 相关章节
  IF 涉及代码 → EXEC: codegraph status .（索引是否最新）
    IF stale → EXEC: codegraph sync .
  IF 涉及第三方 API → EXEC: exa_search("<技术> <版本> docs")
  IF 涉及 Web 运行行为 → 确认应用可启动，准备 DevTools

STEP 3: 确定你在流程的哪个位置
  无 active task + 新需求 → 进入 §3 Open
  S 级（明确根因的小 bug/迭代，§1.2）→ 不进 §3，直接 定位→改→定向验证→commit
  有 active task + prd 已确认 + 无 design → 进入 §4 Design
  有 active task + design 已确认 → 进入 §5 Build
  有 active task + 实现已完成 → 进入 §6 Verify
  有 active task + 验收已通过 → 进入 §7 Archive
```

### 0.2 记忆命中优先级

查找信息时按此顺序：
```
L2: .trellis/tasks/<current>/    → 当前任务的 prd/design/implement
L1: .trellis/spec/                → 跨任务复用规范
L0: CONTEXT.md                    → 项目全貌
L3: .trellis/workspace/           → 仅追溯历史决策时
```
同一事实只保留一个权威来源。发现冲突时以**时间戳新者为准**（task 工件新建于 spec，故 active task 优先；spec 已更新的主题以 spec 新版本为准），并立即报 CONFLICT 待用户裁决，不得自行选边（详见 §7 CONVERGENCE）。

### 0.3 跨 Agent 接手对齐（换 Agent / 新会话强制）

> 换 Agent 应用（Z Code/CodeBuddy/OpenCode/Claude Code/Codex…）或新开会话时，新会话没有旧会话的记忆。**进度只信仓库，不信口头交接**——靠 COMMIT-DISCIPLINE 留下的 git 痕迹 + task 工件对齐。接手即执行 §0.1 STEP 2（基础设施验证→git 状态/分支→task.py current 已合并其中），流程位置由 STEP 3 判定，此处不再重复步骤。

```
RULE: 接手时不得假设"上个 Agent 已完成 X"——一切以 git log + task.py current + §11.4 验证结果为准
RULE: 基础设施半搭就换 Agent 是常态：.trellis 建了但 spec/tasks 空、AGENTS.md 未改占位符、
      CONTEXT.md 未写、组件库未接入——任一命中 → 回 §11 补齐，不自作主张直接进 Build
```

---

## 1. 风险分级与流程路由

### 1.1 分级判定

收到任务后，先判定风险等级。以下任一命中则升级：

```
S → M 升级信号（任一命中则至少 M，再由下判定 M0/M1/M2）:
  - 新增或修改 public API
  - schema 变化（数据库/接口/事件）
  - 跨模块协调（改动超过 1 个模块）
  - 安全边界变化（鉴权/权限/CORS/密钥）
  - 无法可靠回滚

M 内部刻度判定（升级信号命中后，再选最轻满足项）:
  - M0：单文件/单模块、方案明确、有现成可参照实现（即使触及 public API，只要改法是"照已有模式加一个端点/字段"也算 M0）
  - M1：跨模块但方案清晰、无需切片编排（如改 public API 但需改 3 个调用方 + 加测试）
  - M2：全栈/多模块/需切片编排，或"改动牵连 >4 个 API 或 >2 个页面"
  - 判定仍含糊时 → 取更轻一级先动，Execution 中暴露复杂度再升级（逃逸，§9）

M → L 升级信号:
  - 数据迁移（不可逆或需停机）
  - 核心架构变更（框架/数据流/部署形态）
  - 并发/事务逻辑
  - 高不确定性的外部依赖集成
```

### 1.2 流程路由表

| 等级 | 触发条件示例 | 流程步骤 | 验证要求 |
|------|------------|---------|---------|
| **S** | 文案、配置、明确根因的小 bug | 定位→改→定向验证→commit | 相关 lint/test/build + 人工抽查 |
| **M0** | 单文件/单模块、改动明确、有现成参照 | Open(口头确认)→Build→Verify→Archive | `verify` 定向 + 核心路径人工验收 |
| **M1** | 跨模块但方案清晰、无需切片 | Open→Design(一页)→Build→Verify→Archive | `verify` + 核心路径人工验收 |
| **M2** | 全栈/多模块/需切片编排 | Open→Design(含切片)→Build(逐切片)→Verify(串联)→Archive | `verify` + 端到端串联验收 |
| **L** | 数据迁移、鉴权、核心架构、不可逆 | Open→Design→ExecFlow→Build→Verify→Archive | `verify-full` + 独立复核 + 回滚演练 |

> M 内部轻重量刻度（M0 最轻→M2 最重），按"是否写设计文档 / 是否切片"区分，不另立等级体系。
> 升级信号（任一命中则升一级）：新增/改 public API、schema 变化、跨模块、安全边界变化、无法可靠回滚。

**等级变动门禁（强制）**：任务风险等级的上调或下调，都必须由用户在本轮对话中确认。agent 不得自行升级或降级——包括时间盒超时导致的升级（见 §9）。若用户离线/未响应，保持原等级并暂停，不得代决。

### 1.3 完成定义（所有等级通用）

```
DONE = 验收标准全部满足
     AND 自动门禁通过（verify 或 verify-full，按等级）
     AND 核心路径亲测通过
     AND 无未说明的风险
     AND 改动已 git commit
     AND git push 已获用户显式批准（见 §6 PUSH-GATE）
```

---

## 2. 四件套调用规则

### 2.1 工具触发条件

| 工具 | 触发条件 | 命令 |
|------|---------|------|
| Trellis | 全程（任务创建到归档） | `python .\.trellis\scripts\task.py <subcommand>` |
| CodeGraph | 编码前定位、改动前影响面分析 | `codegraph explore "<符号/功能>"` |
| Exa MCP | 遇到不确定的外部技术 | `exa_search("<query>")` |
| Git | 每个逻辑单元完成时（当场提交，禁止攒批） | `git add <file> && git commit -m "<type>(<scope>): <desc>"` |
| DevTools | Web 运行时调试/验收 | `chrome.exe --remote-debugging-port=9222` |

**COMMIT-DISCIPLINE（强制）**：改动完成后**必须当场自动提交**，不得等到任务收尾再批量提交。
- 目的：若对话中断、Agent 崩溃或任务失败，**一眼即可从 git log 看出已做到哪一步、哪一步没落地**——未提交即未做。
- 提交粒度：以「一个逻辑单元」为最小提交（一次定向改动 = 一次 commit），不要用一次大 commit 掩盖多个不相关改动。
- 任务失败处理：若某逻辑单元执行失败，**仍要留下痕迹**——提交一个 `<type>(<scope>): <desc> [WIP-FAIL]` 标记 commit（或在 task 工件记 FAIL 日志 + commit 当前半成品），说明失败点与已做/未做边界，绝不留"静默半完成"状态。
- 严禁：先改一堆文件最后统一 commit；用 `git stash` 代替 commit 躲避提交纪律。

### 2.2 硬规则

```
RULE: 编码前必须执行 codegraph explore，确认没有重复实现
RULE: CodeGraph/Exa/DevTools 的结论必须写入文件，不能只留在对话中
RULE: execute 阶段禁止绕过 prd/implement 改需求
RULE: 禁止 except: return [] / .catch(() => []) 静默吞错
RULE: 垂直切片必须按顺序执行，不允许跳着做
```

---

## 3. Open 阶段（需求澄清）

> S 级（明确根因的小 bug/迭代）不进入本阶段，按 §0.1 STEP 3 直接 定位→改→定向验证→commit。

```
EXEC: python .trellis\scripts\task.py create "<任务名>"
```
1. READ: AGENTS.md + CONTEXT.md + .trellis/spec/ 相关章节
2. 只问会改变范围/验收/技术路线的高价值问题（≤5 个）
3. WRITE: prd.md（附录 A.1）；M0 可仅口头确认需求，不强制写文件
4. IF 外部技术不确定 → EXEC: exa_search → WRITE: research/ 目录
EXIT WHEN: 需求/约束/验收/边界/风险/回滚 齐全 + 用户已确认
  → EXEC: python .\.trellis\scripts\task.py start
  → M0 进 §5 Build；M1/M2/L 进 §4 Design

---

## 4. Design 阶段（技术方案）

按等级裁剪：
```
IF M0 → 跳过 Design，直接 §5 Build
IF M1 → 写一页 design.md（架构/契约/数据流/回滚）
IF M2 → design.md + 切片清单（§4.3）
IF L  → design.md + ExecFlow 清单（§4.4）
```
1. WRITE: design.md（附录 A.2）
2. IF M2/L → 标注协作模式（§4.3），产出切片清单
3. IF 外部技术不确定 → exa_search 落 research/
EXIT WHEN: design.md 齐备 + （M2/L）切片清单齐备 + 用户已确认

### 4.3 前后端协作与切片（M2/L 全栈任务，或 M1 纯前端跨模块任务）

协作模式：
```
IF 核心价值==数据/算法 → 后端先行
IF 核心价值==交互/体验 → 前端先行
IF 两端都重要 → 垂直切片（默认）
```
四条硬规则（替代原 7 个子节）：
```
RULE: 用户路径先于技术分层——先写用户 A→B 行为与结果，再决定页面/API/表
RULE: 契约单一事实源——请求/响应/错误/分页/空值/权限只定义一次；前端类型/client 从契约生成，不手工维护第二份
RULE: 每切片必须真实联调——Mock 仅解耦开发用，完成前去 Mock 接真实后端；UI 字段后端未提供须显示"未提供"，禁止静态数字伪装
RULE: 一个集成负责人——主 Agent 负责契约、迁移顺序、真实联调、最终验收
```
切片操作（M2/L）：
```
1. 拆切片清单（>4 API 或 >2 页 → 再拆；须是可独立验证的用户路径段）
2. FOR EACH slice: EXEC task.py create "slice-NN-<name>" → WRITE slice-prd(附录 A.4) + 接口契约(附录 A.6)
3. Build 阶段按顺序逐切片执行
DB 迁移：expand → migrate/backfill → contract，有回滚方案
```

### 4.4 ExecFlow 清单（仅 L 级）

L 级不可逆变更需要分阶段管控，但**不写重型 execution-flow/phase-ledger/阶段合同**，只用一份轻量清单：
```
WRITE: .trellis/tasks/<task-dir>/exec-flow.md
  - phases: 有序阶段列表，每阶段含 [输入/输出/验证命令/完成定义]
  - 每阶段结束 WRITE: <phase>-result.md（附录 A.7，精简版）
  - 新窗口恢复：READ design.md + exec-flow.md + 上一阶段 result.md
```
> 设计文档与实现的一致性靠 design_hash 比对（在 Verify 阶段 CHECK），不靠独立 ledger 文件。

**PROJECT-SHAPE（项目形态适配，强制）**：agent 创建 task 前识别形态写入 `shape` 字段，据此裁剪。
- Web 全栈：启用契约一致性、前端四态。
- 纯后端/CLI/桌面/脚本（无前端）：跳过四态；契约一致性仅适用于有对外 API/IPC 场景，否则标「不适用」。

---

## 5. Build 阶段（实现）

前置（任一不满足 → BLOCK）：prd 已确认；design 已确认（M0 免）；L 级 exec-flow.md 已批准；CodeGraph 已查影响面（`codegraph explore` + `impact`）。

执行：
```
1. 按 design/切片清单实现，遵守编码红线（§10.1 质量门禁 + §2.2 硬规则）
   - Web 前端逐切片开发中应即时用 DevTools 验证渲染/console/network，不空跑到 Verify 才看浏览器
2. IF 垂直切片: FOR EACH slice (顺序不跳):
     EXEC task.py start <slice>; IMPLEMENT 后端(契约测试覆盖成功+错误路径) + 前端(四态覆盖);
     VERIFY 切片完成; EXEC task.py archive; → 下一切片
3. 范围锁：Build 中禁止改 prd/design/验收；新想法 → change-backlog.md；影响正确性 → STOP 重开 plan
4. 异常处理：契约内错误自修重试 1 次；仍失败上报；超契约范围立即停，等用户决策
```
切片完成定义：
```
[ ] 契约/类型/client 与实现一致，无手工重复类型
[ ] 后端覆盖成功+主要错误路径；响应经 schema 校验
[ ] 前端覆盖 loading/empty/error/success，错误可见可操作
[ ] 本切片不依赖 Mock（前端先行模式允许本切片 UI 用 Mock 完成，但须在 Verify 串联阶段去 Mock 接真实后端，见 §6 第 4 步）
[ ] 从真实入口走通，console/network 无未解释错误
[ ] 变更已 commit（COMMIT-DISCIPLINE §2.1）
```
EXIT WHEN: 实现完成 + 验证通过 → 进 §6 Verify

---

## 6. Verify 阶段（验收）

1. EXEC: verify（L 级 verify-full）
2. IF Web → DevTools 抓 console/network；IF CLI → 复现 + 读日志
3. 同一根因失败 2 次 → 换策略或独立复核（禁第 3 次盲重试）
4. 按协作模式验收：垂直切片→端到端串联；后端先行→API+面板独立；前端先行→去 Mock 真实对接
5. 前端追加：不白屏、console 无未解释 error、错误可见、暗色对比度、契约漂移检查、关键 E2E 走真实入口

### 6.1 理解层审查（核心，非外形审查）

执行顺序：先过 §10.1 自动 GATE（错误可见/白屏/console），再做起码的理解层审查——GATE 是门槛，理解层审查是验收实质。

```
审查 = 追溯链是否真通：需求(prd) → 设计(design/切片) → 实现(代码) → 验证(测试/亲测)
通过条件（缺一不可）：
  - 任取一条验收标准，能指到具体代码行 + 具体测试/命令，且无"假完成"
  - 无 silent clobber / false readiness：未去 Mock、占位页、静态伪装数据均判不通过
  - 禁止用"文档齐全"冒充"做完了"
不通过条件（以下不算通过）：
  - 仅检查排版/命名/格式
  - 仅确认文件存在
```

### 6.2 退出条件

```
EXIT WHEN: 已知 bug 已修 + 用户亲自验收通过
```

**PUSH-GATE（强制）**：任何 `git push` / 部署 / 发布，必须获用户本轮对话明确授权（如"可以推"），不得以 DONE 满足为由自动执行。静默 push 视为严重违规。

---

## 7. Archive 阶段（归档）

1. SYNC: delta spec → 主 spec（CONVERGENCE 双源收敛，见下）
2. EXEC: python .trellis\scripts\task.py archive
3. UPDATE: CONTEXT.md（强制，§7.1）
4. IF 重要架构取舍 → WRITE: ADR
5. EXEC: git add + git commit -m "<type>(<scope>): <desc>"（仅 commit 归档/知识回写相关文件：CONTEXT.md、spec 增量、复盘；业务代码已在 Build 各切片按 COMMIT-DISCIPLINE 当场提交）
6. EXEC: git push（需用户显式批准，见 §6 PUSH-GATE）
7. IF 返工>1 或触发流程逃逸 → WRITE: 复盘（附录 A.8）

**CONVERGENCE（双源收敛，强制）**：同一事实只一个权威来源。task 是 spec 的孵化地，不是并存地。
- task active → 以 task 工件(prd/design) 为权威，spec 暂不改
- task archived → 可复用长期约束 7 天内 delta 到 spec/（已有则更新，否则新建），原工件留作溯源
- L1/L2 同主题冲突 → 以 L2 时间戳新者为准，立即报 CONFLICT 待裁决，不得自行选边

EXIT WHEN: 验收全过 + 门禁绿 + 知识回写完成 + 已 commit（+push 获批）

### 7.1 CONTEXT.md 维护规范

> CONTEXT.md 是项目的"只读此文件即可上手"权威说明。每次任务归档必须同步，确保下一个会话只读 CONTEXT.md 就能定位所有相关文件。

#### 强制更新触发条件

```
RULE: 以下任一发生时，Archive 必须更新 CONTEXT.md（不是可选）：
  - 新增/删除/重命名了源码文件
  - 新增/修改了 API 端点、路由、IPC 通道
  - 数据模型变化（新表/新字段/迁移）
  - 架构/数据流变化（新模块、新依赖、新服务）
  - 发现并解决了重要坑点
  - 功能状态变化（新增/禁用/废弃）
  - 启动/部署方式变化
```

#### 文件结构要求

```
CONTEXT.md 必须包含以下区块（按需扩展，不可缺失）：

1. 一分钟速览：项目是什么、技术栈一行列表
2. 文件索引表：关键文件 + 核心函数/行号 + "何时读"列
   → 目的：AI 拿到任务后按"何时读"列精准定位，不全量扫描
3. 数据流/架构：模块间调用关系（文字或 ASCII 图）
4. 启动与部署：命令、端口、环境变量
5. 坑点表：踩过的坑 + 解法（防重复踩）
6. 当前状态：已完成到哪、正在做什么、下一步
7. 已禁用/废弃功能：避免误触死代码
```

#### 维护规则

```
RULE: "何时读"列是核心——每个文件必须标注"改什么功能时需要读它"
RULE: 行号/函数名必须与实际代码一致，过时即更新（不攒着）
RULE: 新文件加入索引，删除的文件从索引移除
RULE: 坑点只记有解法的（问题+根因+修复），不记未解决的
RULE: 当前状态区块每次归档刷新（一两行即可）
RULE: 文件总长控制在 300 行以内，超出则拆分子文档并在索引表引用
RULE: S 级改动（改个文案/配置）可跳过，但 M/L 级必须更新
```

---

## 8. 临时需求与范围锁

执行中新需求：
```
1. WRITE: change-backlog.md（来源/价值/影响/紧急性）
2. 不影响正确性 → 延期到下一 task
3. 影响正确性 → STOP，重开 plan 做版本化评估
RULE: 严禁在 Build 中临时改总体设计/接口契约/验收标准
```

---

## 9. 流程逃逸与时间盒

逃逸（动态升降级）：
```
S→M: 补需求卡继续
M→L: STOP 实现 → 补 Design + 回滚方案 → 继续
L→M: 需用户确认风险可接受 → 简化
RULE: 不允许静默降级；逃逸记入归档复盘
```

时间盒（超时均**提议**对应动作，需用户确认，不得代决）：

| 场景 | 上限 | 超时处理 |
|------|------|---------|
| S 全流程 | 30 min | 提议升 M，补需求卡 |
| M Design | 2 h | 提议降 S 做最小版 或 拆两个 M |
| L Design | 1 day | 提议拆分任务，各子任务独立可交付 |
| 单切片实现 | 4 h | STOP，分析契约问题还是实现问题 |
| 同根因调试 | 2 次 | 换策略或升级，禁第 3 次盲重试 |

---

## 10. 质量门禁

### 10.1 通用交付门槛

```
GATE: 需求可验证（验收标准/不做项/风险/回滚已说明）
GATE: 自动门禁通过（format/lint/typecheck/unit/integration/build 适用项）
GATE: 错误可行动（分类+保留原因+用户可见反馈；禁止静默吞错）
GATE: 契约一致（schema/client/Mock/真实响应一致）
GATE: 数据安全（密钥不入库；迁移可恢复；重要数据有备份）
GATE: 单点定义（配置/常量/枚举/token 不跨层重复）
GATE: 真实路径验收（核心路径从真实入口走通）
GATE: 版本可恢复（已 commit+push；L 级演练回滚）
```

### 10.2 统一验证入口

```
verify      = format-check + lint + typecheck + unit + integration + build
verify-full = verify + security + migration-check + critical-e2e + rollback-check
```

```
IF S 级 → 跑定向测试
IF M 级 → EXEC: <项目约定验证命令>（如 package.json scripts / Makefile target / pytest / 自定义，见项目 CONTEXT.md）
IF L 级 → EXEC: <项目约定全量验证命令>（verify-full 或等价）
RULE: 测试失败修根因，禁止跳过测试或放宽断言制造绿色
```

### 10.3 技术栈 Profile

```
React Web:
  - 组件库+token（shadcn/ui 默认）
  - 数据获取统一方案（如 React Query）
  - 四态覆盖（loading/empty/error/success）
  - 关键路径 E2E 走真实 UI

Python API:
  - schema 校验（pydantic/marshmallow）
  - 结构化错误（error_type + message + request_id）
  - 单元/集成测试
  - 依赖锁定

SQLite（仅项目确实使用时）:
  - WAL + 写锁
  - 迁移 + 断点续跑

CLI/桌面:
  - golden test / 文件样例 / 退出码验证
  - 不强制 Web 组件库
```

### 10.4 前端组件库策略

```
RULE: 组件库与 token 在 bootstrap 阶段已接入（§11.1）；Build 中禁止散落硬编码样式，复用已装组件/token
RULE: 首个前端任务卡验证「组件库/token/spec/验证命令已就位」即可，不重复登记 bootstrap 已完成的事

推荐: shadcn/ui（npx shadcn@latest init）
  - 源码归你（src/components/ui/）
  - Tailwind + Radix（免手写 CSS + 无障碍）
  - 内置暗色模式
  - 50+ 组件覆盖工具型应用 90% 场景
  - Tailwind theme = 唯一颜色系统

备选: Ant Design（表格密集）/ MUI（Material）/ Tailwind 裸写（1-2 页）
```

### 10.5 复杂工程高频反模式（审查重点）

反复修改 / 长链路工程里常出现、且容易被当成"做完了"的 bug 类型。理解层审查（§6.1）应逐条对照排查——它们正是 `false readiness` 与 `silent clobber` 的具体形态。

| 类型 | 中文理解 | 典型表现 | 解法方向 |
|------|---------|---------|---------|
| Pipeline stage regression | 流水线后级把前级改对的结果又搞坏 | 上游修好的字段，下游又被重写覆盖 | 后级只信任前级签名，不自己再"修"一遍 |
| Invariant violation at boundaries | 关口不守同一条不变量 | "章不能超长"在 A 处校验、B 处又放宽为不校验 | 不变量收敛到 single enforcement point |
| Silent clobber / last-writer-wins | 后写覆盖前写，静默盖掉正确数据 | 并发/多次保存，最后一次覆盖前面的正确值 | 显性合并或加锁，禁止静默 last-write-wins |
| False readiness / premature completion | 假完成 | 说就绪了，其实只等到占位页 / Mock 未去 | 完成定义不含占位/Mock，Verify 串联真实路径 |
| Leaky pipeline | 各阶段各写一套规则，没统一出口 | 同一条不变量每阶段各实现一次，相互漂移 | 收敛到 canonical 出口（schema validation / 统一 normalize 层） |

> 共同点：同一条不变量没有 single enforcement point，各阶段各写各的。统一解法是把不变量收敛到一个 canonical 出口，后级只消费前级产物，不重复实现校验/转换。

---

## 11. 新建项目接入

### 11.0 目录结构总览

Bootstrap 完成后，项目根目录应出现以下结构：

```
my_project/
├── .trellis/              ← 治理层（spec + tasks + scripts）
├── .codegraph/            ← 代码索引（codegraph init 生成，绝不复制）
├── .agents/               ← 跨 Agent 共享技能层
│   └── skills/            ← 所有 Agent 读同一份 skill
├── .codebuddy/            ← CodeBuddy 配置（主力 IDE Agent）
├── .claude/               ← Claude Code 配置（CLI 强规划/大重构）
├── .codex/                ← Codex 配置（疑难 bug 二次意见）；技能读取 .codex/skills/（或共享 .agents/skills/）
├── .zcode/                ← Z Code 配置（桌面 AI 编程软件，类 OpenCode）
├── .opencode/             ← OpenCode 配置（轻量辅助）
├── contracts/             ← 契约文件（OpenAPI/JSON Schema，契约驱动项目必须）
├── AGENTS.md              ← 项目治理入口（所有 Agent 共读）
├── CONTEXT.md             ← 项目权威概览
└── frontend/              ← Web 项目前端（按需）
```

> QClaw 使用全局配置 `~/.qclaw/openclaw.json`，skills 通过 `skills.load.extraDirs` 指向项目 `.agents/skills/` 或独立 `.qclaw/skills/`。
> WorkBuddy 读取项目根目录的 `AGENTS.md` + `.trellis/`，无需独立配置目录。
> QoderWork CN 使用全局配置 `~/.qoderworkcn/`，选择项目文件夹即接入，读取 `AGENTS.md` + `.trellis/`。

### 11.1 Bootstrap 执行脚本

```powershell
# ═══════════════════════════════════════════════════════════
# 项目级搭建（每个新项目执行一次）
# ═══════════════════════════════════════════════════════════

# 注: 下列脚本依赖 $env:AI_PROJECT_TEMPLATE 中的 `.trellis/scripts/`，
# 运行环境需 Python 3.10+。若模板未配置，可手动创建 `.trellis/` 空骨架
# （workflow.md + spec/ + tasks/）替代，再补齐，不必卡在 bootstrap。

# 1. 建目录 + git
New-Item -ItemType Directory D:\projects\my_project
Set-Location D:\projects\my_project
git init -b main

# 2. 复制治理层（排除运行时数据：tasks/ workspace/）
# 模板缺失不中断：跳过模板复制，手动建骨架（.trellis/spec/tasks + AGENTS.md + CONTEXT.md + .agents/skills/）
$sourceProject = $env:AI_PROJECT_TEMPLATE
if (!$sourceProject -or !(Test-Path $sourceProject)) { $sourceProject = $null; Write-Host "AI_PROJECT_TEMPLATE 未配置：手动建骨架，跳过模板复制" }

if ($sourceProject) {
    if (!(Test-Path '.trellis')) {
        Copy-Item -Recurse -Force (Join-Path $sourceProject '.trellis') '.trellis'
        # 清除从模板带入的任务和会话记录（这是运行时数据，不可跨项目）
        Remove-Item -Recurse -Force '.trellis\tasks' -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force '.trellis\workspace' -ErrorAction SilentlyContinue
    }
    foreach ($f in @('AGENTS.md', 'CONTEXT.md')) {
        if (!(Test-Path $f)) { Copy-Item -Force (Join-Path $sourceProject $f) $f }
    }
}
New-Item -ItemType Directory -Force '.trellis\tasks\archive' | Out-Null
New-Item -ItemType Directory -Force '.trellis\workspace' | Out-Null

# 3. 共享 Agent 技能层（所有 Agent 读同一份）
if ($sourceProject -and !(Test-Path '.agents')) { Copy-Item -Recurse -Force (Join-Path $sourceProject '.agents') '.agents' }

# 4. 各 Agent 配置目录（按你在用的逐个接入）
$agentDirs = @('.codebuddy', '.claude', '.codex', '.opencode', '.zcode')
foreach ($name in $agentDirs) {
    $src = Join-Path $sourceProject $name
    $dst = Join-Path (Get-Location) $name
    if ($sourceProject -and !(Test-Path $dst) -and (Test-Path $src)) {
        Copy-Item -Recurse -Force $src $dst
    }
}
# QClaw: 全局配置，不建项目级目录
# 确认 ~/.qclaw/openclaw.json 的 skills.load.extraDirs 包含本项目 .agents/skills 路径
# WorkBuddy / QoderWork: 读 AGENTS.md + .trellis/，无需独立目录

# 5. Trellis 开发者身份（无模板骨架缺 scripts/ 时，agent 手动补等价配置即可）
python .trellis\scripts\init_developer.py dev

# 6. CodeGraph（绝不复制，只 init）
codegraph init .
codegraph status .

# 7. 契约目录（全栈/契约驱动项目必须）
New-Item -ItemType Directory -Force contracts
# 放入 OpenAPI/JSON Schema 文件 + 验证脚本

# ═══════════════════════════════════════════════════════════
# 仅 Web 前端项目执行
# ═══════════════════════════════════════════════════════════

# 8. 前端目录
New-Item -ItemType Directory -Force frontend\src\components frontend\src\pages frontend\src\api frontend\public

# 9. 组件库（基础设施决策，不是后补）
Set-Location frontend
npm create vite@latest . -- --template react-ts
npm install react@^19 react-dom@^19 react-router-dom@^7 @tanstack/react-query@^5
npm install -D typescript@~6 vite@^8 @vitejs/plugin-react@^6 tailwindcss@^4 @tailwindcss/vite@^4
npx shadcn@latest init -d
npx shadcn@latest add button select input textarea dialog tabs card badge table dropdown-menu
Set-Location ..

# 10. 复制脚手架配置（从模板；无模板时 agent 手动写这几个文件）
if ($sourceProject) {
    $feSource = Join-Path $sourceProject 'frontend'
    foreach ($feFile in @('vite.config.ts', 'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json')) {
        $src = Join-Path $feSource $feFile
        if (!(Test-Path "frontend\$feFile") -and (Test-Path $src)) { Copy-Item -Force $src "frontend\$feFile" }
    }
    if (!(Test-Path 'frontend\src\index.css')) {
        Copy-Item -Force (Join-Path $feSource 'src\index.css') 'frontend\src\index.css'
    }
}

# 11. foundation.json（Web 项目必须）
# 填写: framework, componentLibrary, tokenSource, contractSource,
#        deliveryState(="prototype"), verifyCommand, browserSmokeCommand

# 12. AGENTS.md 追加 CONTEXT.md 维护规则（TRELLIS:END 之后；与 §7.1 规范同源，改 §7.1 必须同步此处，防漂移）
$rule = @"

## CONTEXT.md 维护（强制）

- 任务归档时必须同步 CONTEXT.md（M/L 级强制，S 级可跳过）
- 触发条件：新增/删除/重命名源码文件、API/路由/IPC 变化、数据模型变化、架构变化、新坑点、功能状态变化、启动方式变化
- 文件索引表必须有"何时读"列——标注"改什么功能时需要读它"
- 行号/函数名与实际代码一致，过时即更新
- 新文件加入索引，删除的文件移除
- 当前状态区块每次归档刷新
- 总长 ≤ 300 行，超出拆子文档
"@
if (!(Select-String -Path AGENTS.md -Pattern "CONTEXT.md 维护" -Quiet)) {
    Add-Content -Path AGENTS.md -Value $rule
}
```

### 11.2 Agent 接入速查

| Agent | 配置位置 | 读取什么 | 接入方式 |
|-------|---------|---------|---------|
| **CodeBuddy** | `.codebuddy/` | AGENTS.md + .trellis/ + .agents/skills/ | 从模板复制 |
| **Claude Code** | `.claude/` | AGENTS.md + .trellis/ + .agents/skills/ | 从模板复制 |
| **Codex** | `.codex/` | AGENTS.md + .codex/skills/ | 从模板复制 |
| **OpenCode** | `.opencode/` | AGENTS.md + .trellis/ | 从模板复制 |
| **Z Code** | `.zcode/` | AGENTS.md + .trellis/ | 从模板复制（桌面 AI 编程软件，类 OpenCode/Claude Code/Codex/CodeBuddy） |
| **QClaw** | `~/.qclaw/openclaw.json`（全局） | extraDirs → .agents/skills/ | 修改全局配置指向项目 |
| **WorkBuddy** | 无独立目录 | AGENTS.md + .trellis/（或 .codebuddy/） | 确认读取路径即可 |
| **QoderWork CN** | `~/.qoderworkcn/`（全局） | AGENTS.md + .trellis/ | 选择项目文件夹即接入 |

```
RULE: 所有 Agent 读同一份 .trellis/ + AGENTS.md + CONTEXT.md
RULE: .agents/skills/ 是共享技能的单一来源，各 Agent 配置目录不重复存放
RULE: 新增/移除 Agent 时更新本表 + AGENTS.md 中的 Agent 列表
RULE: 接入后必须重启对应 Agent 应用，否则 hook/skill 不生效
RULE: 接入清单含配置目录验证：Test-Path .zcode（Z Code）
```

### 11.3 复制后必须手动改

| 文件 | 改什么 |
|------|--------|
| `CONTEXT.md` | 项目名/技术栈/数据流/坑点（保留结构换内容，**删除所有占位符**） |
| `AGENTS.md` | 当前项目名和业务描述 + Agent 列表 + **必须含「CONTEXT.md 维护（强制）」规则块**（§7.1） |
| `frontend/vite.config.ts` | proxy.target 端口 |
| `.trellis/spec/frontend/index.md` | 设计 token 值、导航结构 |
| `~/.qclaw/openclaw.json` | extraDirs 追加本项目 .agents/skills 路径 |

### 11.4 基础设施有效性验证

> 不只是"文件存在"——空壳模板不算接入成功。**任何验证未通过不得进入 Build。**

```powershell
# ═══ 结构验证 ═══
Test-Path .trellis/scripts/task.py
Test-Path .trellis/spec
Test-Path CONTEXT.md
Test-Path .codegraph
Test-Path .agents/skills
Test-Path contracts              # 仅契约驱动项目（有对外 API/前后端契约）；无契约项目此项不适用，跳过

# ═══ Agent 配置验证 ═══
Test-Path .codebuddy             # CodeBuddy
Test-Path .claude                # Claude Code
Test-Path .codex                 # Codex
Test-Path .opencode              # OpenCode
Test-Path .zcode                  # Z Code
# QClaw: 确认 ~/.qclaw/openclaw.json 中 extraDirs 包含本项目路径
# WorkBuddy/QoderWork: 确认能读到 AGENTS.md（打开项目后测试）

# ═══ 内容有效性（不是空壳）═══
Select-String -Path CONTEXT.md -Pattern "技术栈|Tech Stack" -Quiet    # 应为 True
Select-String -Path CONTEXT.md -Pattern "\[TODO\]|\[待填\]|<项目名>" -Quiet  # 应为 False
Select-String -Path AGENTS.md -Pattern "模板项目|template" -Quiet    # 应为 False
Select-String -Path AGENTS.md -Pattern "CONTEXT.md 维护" -Quiet      # 应为 True（§7.1 规则块）
Select-String -Path CONTEXT.md -Pattern "何时读" -Quiet              # 应为 True（文件索引表）

# ═══ 功能验证 ═══
python .trellis/scripts/task.py list          # 正常返回不报错
# 隔离性验证：tasks/ 下不得有模板项目的遗留任务
$foreignTasks = Get-ChildItem '.trellis\tasks' -Directory | Where-Object { $_.Name -ne 'archive' }
if ($foreignTasks) { throw "隔离性失败: tasks/ 含非本项目任务: $($foreignTasks.Name -join ', ')。请清除后重试。" }
python .trellis/scripts/task.py create "验证测试"
python .trellis/scripts/task.py archive
codegraph status .                            # [OK]
codegraph files                               # 返回本项目文件，不串项目

# ═══ Web 项目额外 ═══
# foundation.json 字段完整：framework/componentLibrary/tokenSource/deliveryState/verifyCommand
cd frontend; npm run build; cd ..             # 构建通过
```

```
RULE: 任何验证未通过 → 不得进入 Build
RULE: .codegraph/ 绝不从别的项目复制
RULE: .trellis/tasks/ 和 .trellis/workspace/ 是运行时数据，绝不从模板复制（bootstrap 只复制 scripts/ + spec/）
RULE: package.json 不复制，用 npm create vite@latest 生成
RULE: .agents/skills/ 从模板复制后按项目需要增删 skill
```

### 11.5 定期健康检查

```
EVERY month OR after toolchain change:
  - EXEC: codegraph status . → IF stale → codegraph sync .
  - CHECK: CONTEXT.md 结构完整且与实际代码一致（§7.1 七个区块不缺、行号/文件索引不过时）
  - CHECK: spec 与实际代码一致（新增 API/页面是否已登记）
  - CHECK: .agents/skills/ 是否有过期 skill（引用了不存在的命令/路径）
  - CHECK: 各 Agent 配置目录是否同步（新增 skill 是否所有 Agent 都能读到）
```

### 11.6 已有项目批量扩散

```
FOR EACH project IN D:\projects:
  - 缺什么补什么（.trellis/.agents/.codebuddy/.claude/.codex/.opencode/.zcode）
  - AGENTS.md/CONTEXT.md 已存在 → 不覆盖
  - AGENTS.md 缺「CONTEXT.md 维护（强制）」规则块 → 追加（§11.1 步骤 12）
  - CONTEXT.md 缺 §7.1 七区块 → 按规范补全
  - .codegraph/ → codegraph init（不复制）
  - .agents/skills/ 缺失 → 从模板复制
  - 扩散后运行 §11.4 验证
  - 不通过 → 单独修复
```

---

## 12. 流程健康度

### 12.1 失败复盘

```
TRIGGER: M/L 级任务 Archive 时，IF 返工 > 1 OR 触发过流程逃逸
WRITE: 附录 A.8 复盘模板 → task 归档文件
RULE: 顺利完成的任务不强制写
```

### 12.2 四个可行动指标

```
EVERY task end, RECORD:
  1. 首次验收是否通过: yes/no
  2. 交付耗时: <actual> vs <时间盒>
  3. 返工次数: <count>
  4. 逃逸缺陷（验收后才发现）: yes/no + 描述

EVERY month, REVIEW:
  - 同一类问题反复出现 → WRITE: spec 作为硬规则
```

### 12.3 文档维护

```
RULE: 本文档是稳定方法论，只在核心原则变化时修订
RULE: 环境配置（Agent/模型/券/平台）→ 放各项目 CONTEXT.md 或 ENV.md
RULE: 工具链变动 → 只影响 §11 和附录 B，不影响 §0-§10 逻辑
```

---

## 附录 A：文件模板

### A.1 prd.md

```markdown
# <Task Name> - PRD

## 需求
我要解决什么问题：（一句话）

## 具体例子
给 ___ 输入，期望 ___ 输出

## 验收标准
- [ ] <可验证检查项 1>
- [ ] <可验证检查项 2>

## 不做项
- <这版明确不做什么>

## 影响范围
- 影响哪些现有功能：<列表>

## 风险与回滚
- 风险等级：S / M0-M2 / L
- 不可逆操作：<列表>
- 回滚方式：<描述>
```

### A.2 design.md

```markdown
# <Task Name> - Design

## 架构
<模块划分、数据流、技术选型>

## 契约
<OpenAPI/JSON Schema 摘要或文件路径>

## 数据模型
<表结构/Schema 变更>

## 回滚形态
<如果出问题怎么回退>

## 协作模式
<后端先行 / 前端先行 / 垂直切片>
```

### A.3 implement.md

```markdown
# <Task Name> - Implementation Plan

## 执行清单
1. [ ] <步骤 1> → 验证: <命令>
2. [ ] <步骤 2> → 验证: <命令>

## 切片清单（M2/L 垂直切片，可选）
- slice-01: <名称> | API 2-4 | 页 1-2 | 依赖 无
- slice-02: <名称> | API 2-4 | 页 1-2 | 依赖 slice-01
```

### A.4 slice-prd.md

```markdown
# <Slice-N>: <Name>
> Parent: <parent-task-dir>

## User Path
<从哪出发 → 做什么 → 成功看到什么 → 失败看到什么>

## Acceptance Criteria
- [ ] <可验证检查项>
- [ ] 集成：去 Mock，连接真实后端通过

## API Contract
| Method | Path | Request | Success | Errors |
|--------|------|---------|---------|--------|
| POST | /api/xxx | {field} | {result} | 400/413/500 + error_type |
```

### A.5 slice-implement.md

```markdown
# <Slice-N> Implementation Plan

## Frontend Tasks
- [ ] <页面/组件>: <做什么> —— 覆盖 loading/empty/error/success

## Backend Tasks
- [ ] <端点/函数>: <做什么> —— 契约测试覆盖成功+错误路径

## Done Checklist
- [ ] 前端四态覆盖
- [ ] 后端契约测试通过
- [ ] 联调通过（去 Mock）
- [ ] 验收标准全过
- [ ] console/network 无未解释错误
- [ ] 变更已提交
```

### A.6 接口契约模板（每切片必须产出）

````markdown
# 切片 N：<路径段名称> 接口契约

## 端点

### POST /api/xxx
**用途**：<这个端点做什么>
**权限**：<匿名/登录/角色/资源所有者>
**幂等性**：<是否幂等，重复提交如何处理>
**请求体**：
```json
{ "field1": "string", "field2": "string[]" }
```
**成功响应** (200)：
```json
{ "result": [{ "id": "int", "name": "string", "status": "ok|error" }] }
```
**错误响应**：
| HTTP 状态 | error_type | 含义 |
|-----------|------------|------|
| 400 | invalid_input | 请求参数不合法 |
| 413 | file_too_large | 文件超过限制 |
| 500 | internal | 服务端内部错误 |

## 边界约定
- 空列表返回 200 + `result: []`，不是 404
- 超时 30s 返回 `error_type: "timeout"`
- 分页：`?page=1&size=20`，返回 `{ items: [], total: int, page: int, size: int }`
- 兼容性：新增字段向后兼容；删除/改类型需要版本迁移
- 可观测性：错误响应带 request_id，日志可按 request_id 追踪
````

### A.7 阶段结果（仅 L 级 exec-flow 各阶段用，精简版）

```markdown
# Phase <id> Result
## 改动: <列表>
## 输出: <文件/路径>
## 验证: 命令 <cmd> / exit <code> / 断言 <pass/fail+detail>
## 偏差: <与计划差异>
```

### A.8 复盘模板

```markdown
## 复盘
- 分级是否准确：S / M0-M2 / L → 实际复杂度
- 返工次数与根因：需求不清 / 证据不足 / 实现错误 / 环境问题
- 时间盒是否触发：哪个阶段超时，如何处理
- 流程逃逸是否发生：从哪升到哪，补了什么
- 可沉淀的规则：（写入 spec 或 CONTEXT 坑点表）
```

---

## 附录 B：命令速查

```bash
# —— Trellis 任务流 ——
python .\.trellis\scripts\task.py create "<任务名>"
python .\.trellis\scripts\task.py start
python .\.trellis\scripts\task.py start <slice-child-task>
python .\.trellis\scripts\task.py current
python .\.trellis\scripts\task.py list --mine
python .\.trellis\scripts\task.py archive

# —— Trellis 记忆 ——
python .\.trellis\scripts\get_context.py
python .\.trellis\scripts\get_context.py --mode packages
python .\.trellis\scripts\add_session.py --title "T" --commit "hash" --summary "S"

# —— CodeGraph ——
codegraph init .                    # 项目级一次性
codegraph status .                  # 索引是否 up to date
codegraph sync .                    # 增量同步
codegraph files                     # 文件结构
codegraph explore "功能/符号"       # 编码前必跑
codegraph callers "函数名"          # 谁调用它
codegraph callees "函数名"          # 它调用谁
codegraph impact "函数名"           # 改它影响谁

# —— Git ——
git status
git diff --stat
git add <file>
git commit -m "<type>(<scope>): <desc>"
git worktree add ../<project>-<name> feat/<name>

# —— Chrome DevTools（按需）——
chrome.exe --remote-debugging-port=9222

# —— 验证入口（项目 package.json scripts 或 Makefile target）——
npm run verify        # format+lint+type+unit+integration+build
npm run verify-full   # verify + security+migration+e2e+rollback

# 回退: 若项目无 package.json scripts / Makefile target，agent 应改用项目实际
# 构建命令（从 README/CONTEXT.md 读取），并在 task 中记录所用命令，
# 而非假定 npm/pytest 存在。
```

### Git 提交格式

```
<type>(<scope>): <描述>
type: feat / fix / refactor / docs / test / chore / perf
```

### 提交时机

| 阶段 | Git 动作 |
|------|---------|
| Plan 产物可复用 | commit |
| Design 定稿 | `docs: prd/design 定稿` |
| Execute 中 | 一个逻辑单元一个 commit |
| Finish 完成后 | 必须 commit + push |

### 分支策略

| 场景 | 策略 |
|------|------|
| 简单改动/Bug | 直接在 `main` |
| 复杂任务/跨会话 | `git worktree add ../<project>-<name> feat/<name>` |
| 紧急 hotfix | 切回 `main` 修完，再 rebase 回 worktree |


