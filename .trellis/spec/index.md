# Spec Index

> 跨任务复用的长期规范入口。按 package/layer 组织，每个 index.md 含 Pre-Development Checklist 与 Quality Check。

## Packages

| Package | 入口 | 范围 |
|---|---|---|
| backend | [backend/index.md](backend/index.md) | FastAPI + Socket.IO + services + db + routers + tests |
| frontend | [frontend/index.md](frontend/index.md) | Vue3 SPA（views/components/stores/utils） |

## 何时更新 spec

- 新模式/约定被发现（重复出现 2 次以上才固化，一次性技巧不进 spec）
- bug 修复产生可复用的预防规则
- 新技术决策落地
- 详见根目录 `AI_AGENT_WORKFLOW.md` §7 CONVERGENCE（task 是 spec 孵化地，不是并存地）
