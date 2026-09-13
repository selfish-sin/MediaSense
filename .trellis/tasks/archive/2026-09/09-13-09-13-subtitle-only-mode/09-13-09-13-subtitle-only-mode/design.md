# 09-13-subtitle-only-mode - Design v3（高级选项整体重构）

> v2 遗留矛盾（用户指出）：任务目标模式与「输出素材」勾选框语义打架。
> 调查结论：output_options 是**全死控件**——提交后后端零消费、前端结果区/历史页也不读；
> HomeView 默认值 `['result_md']` 连 srt/txt 都没勾，若它生效早就不产字幕了。
> 五个勾选框里唯一有真实后端对应物的是「视频 (.mp4)」→ 既有 options.download_video /
> download_video_only（_acquire_audio 单点），但前端从未发送过。

## 设计原则

同一条不变量只允许一个控制点（workflow §10.5：single enforcement point）。
整卡按任务管线重排：**要什么 → 文本哪来 → AI 怎么加工**，消灭跨区重复表达。

## 新结构（3 区，纯前端，零后端改动）

```
高级选项
├─ ① 任务目标（原处理流程的模式卡独立成区）
│    [◉ 总结成文] [○ 仅生成字幕]
│    提示块（仅字幕模式，同 v2）
│
├─ ② 文本获取与转录（原「语音转录」扩容，吸收处理流程剩余开关）
│    文本来源: ◉自动：优先平台字幕，没有就转录 ○直接转录生成
│              ★ 全模式常显（合并原「强制转录」tile——同一 force_transcribe 不再两处控制）
│              hint: 仅视频链接任务生效，本地文件自动转录
│    转录引擎 / Whisper模型+设备（仅local）/ 转录语言   （原样迁移）
│    ☐ 连视频一起保留（默认关=仅音频）
│              ★ 让「视频(.mp4)」起死回生：接通既有 options.download_video，
│                提示大文件较慢；仅字幕+自动来源拿到字幕时不发生下载，此项不生效（hint 写明）
│    ☐ 说话人分离（仅本地 Whisper 生效）                （从处理流程迁入，转录域归位）
│
└─ ③ AI 加工（原「AI 模型」区 + 处理流程的 AI 开关合并）
     总结/校正模型 select（含校正默认 placeholder，原样迁移）
     ☐ AI 校正 + 校正领域/校正模型子选项（原样）
     ☐ 转录预览（仅总结成文模式显示）
```

## 删除项与接线

| 删除 | 理由 |
|---|---|
| 「输出素材」整区 5 个勾选框 | 死 UI；srt/txt/md 本就必然产出并在结果页列出，无需勾选 |
| 「强制转录」tile | 被 ② 文本来源常显 radio 取代（单一控制点） |
| 处理流程区（区块本身） | 模式→①、来源/媒体→②、校正/预览→③，管线叙述取代大杂烩 |
| outputOptions model/payload/restore 回填 | 死代码出清（useJobRestore/HomeView/HomeAdvancedOptions 三处） |

新增接线（均走既有后端选项，零后端改动）：
- `advancedOptions.download_video: false`（默认）→ 提交进 options → `_acquire_audio` 既有分支生效
- 历史 options 无此键 → restore 合并后仍默认 false，天然兼容

## 模式 × 控件可见性矩阵（重构后唯一事实）

| 控件 | 总结成文 | 仅生成字幕 |
|---|---|---|
| 任务目标提示块 | 隐 | 显 |
| 文本来源 | 显（链接生效） | 显（链接生效） |
| 连视频保留 / 引擎 / 语言 / 说话人分离 | 显 | 显 |
| AI 校正(+子选项) | 显 | 显 |
| 转录预览 | 显 | 隐 |
| AI 模型 | 显 | 显（校正可能用） |

矛盾消除：模式决定"有没有 md"；产出物不再有第二处开关；force_transcribe 单点；
媒体下载从假选项变真选项且诚实标注生效条件。

## 实现面

1. `HomeAdvancedOptions.vue`：整卡模板/样式重构（3 区），删 outputOptions defineModel
2. `HomeView.vue`：删 outputOptions ref/payload 两处/v-model 绑定
3. `useJobRestore.js`：删 outputOptions 依赖与回填
4. `settingsStore.js`：advancedOptions 增 `download_video: false`
5. 测试：settingsStore 增 download_video 默认值用例；vitest/lint/verify.py；浏览器 E2E（UI 联动 + 真实B站仅字幕任务回归）

## 回滚

纯前端 git revert 单提交；无契约/schema 变化。

---

## v4 定稿（用户拍板：功能全保留，输出素材做成真选项放进模式卡下）

在 v2 基础上重构（不回退，v2 已提交 ff554bd）：

- 三区管线：①任务目标（模式卡 + 输出素材随模式展开）②文本获取与转录（文本来源常显，
  吸收「强制转录」tile；说话人分离迁入）③AI 加工（AI 模型迁入 + AI 校正 + 转录预览）
- **输出素材从死 UI 变真**（调查结论：output_options 此前后端/前端零消费，纯装饰）：
  - 勾选 = 结果页「下载素材」实际过滤展示（HomeResultPanel jobFiles 计算过滤；
    txt 组覆盖 txt_clean/txt_raw；未登记键不过滤）
  - 「视频」勾选 → 提交时映射既有 options.download_video（真实下载视频原件，默认关）
  - 仅字幕模式：md 项隐藏、srt 锁定勾选（watcher 强制包含）
  - outputOptions 收进 settingsStore（HomeView ref / defineModel / prop 中转全部出清）
- **顺手修真 bug**：HomeView onAiDone 成功分支从不拉取结果——总结任务靠流式 token 填充
  结果区，仅字幕任务无流式输出 → 完成后结果区显示「暂无结果」。补：resultText 为空时
  `jobStore.loadJob(job_id)` 回填（result_text=转录文本，下载素材随之可用）。
- 历史 options 无 download_video 键 → restore 默认 false，天然兼容。

E2E 实证：三区渲染 ✓；模式联动（md 隐藏/srt 锁定/预览隐藏/按钮文案）✓；
真实B站任务 do_summary=false + download_video=false ✓；下载素材过滤（取消 txt →
txt+txt_clean 消失，恢复复现）✓。
