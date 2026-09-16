# chat-panel-chat-features - PRD

## 需求

追问面板只有「编辑 / 重新生成 / 删除」三个纯文字按钮，没有主流 AI 聊天的基础能力。
补齐标准聊天交互（不含 TTS）。

## 验收标准

- [ ] 每条 AI 回答可一键复制 Markdown 原文；**有引用来源时**复制按钮变下拉，可选「仅正文 / 正文 + 参考来源」
- [ ] 每条用户提问可复制 / 编辑 / 删除（删除为**整轮删除**：提问及其回答一起删，不留孤儿回答）
- [ ] 每条消息显示时间戳，且与落库时间一致（不差时区偏移）
- [ ] 会话级「复制对话」→ Markdown：`### 你` / `### AI` 分节，AI 回答保留原文，来源以「**参考来源**」附在该条末尾
- [ ] Markdown 代码块右上角一键复制，且**结果区 / 工作台结果卡同样生效**（不是只在追问里能用）
- [ ] 复制统一走 `utils/clipboard.js` 的 `copyText()`，不新增第二份剪贴板实现
- [ ] vitest / lint / build 全绿；浏览器 console 无新增错误或警告

## 不做项

- TTS 语音朗读
- 消息编辑后原地重发（沿用既有「编辑 = 回到该提问点 + 填入输入框」语义）
- 长回答折叠、会话级 token 汇总、导出对话为 .md 文件下载（当时选了推荐套餐，未选全家桶）

## 技术要点（已实现）

- 格式化/解析抽为纯函数 `utils/chatExport.js`，组件只调 `copyText()`
- 代码块按钮无 onClick（v-html 注入绑不上 Vue 事件）→ `utils/codeCopy.js` 在 document 挂一次委托
- `job_chats.created_at` 是 SQLite UTC 无时区标记 → `parseChatTime()` 补 `Z` 按 UTC 解析

## 风险与回滚

- 风险等级：M1（跨 4 个前端文件 + markdown 渲染层，无后端/数据模型变更）
- 回滚：`git revert` 对应提交；代码块只是多包一层 `div`，回退不影响既有 markdown 结构
