# 质量优化：实时推送/日志修复/巨型文件拆分 - Implementation Plan

## 执行清单

1. [x] slice-1：HomeView 日志增量回退 + catch 提示 + 死代码删除 → 验证: `cd frontend && npm run test && npm run build`
2. [x] slice-2：后端 batch_progress emit + 前端 socket 订阅（含降级轮询） → 验证: `backend pytest` + `frontend npm run test && npm run build` + 新增后端 emit 单测
3. [x] slice-5：补 composable 测试 → 拆 useBackendLogs/useBatchProgress/useJobRestore → HomeView 瘦身 → 验证: `frontend npm run test && npm run build`
4. [x] slice-3：_process_job_impl 拆策略函数（信号量/取消语义不动） → 验证: `backend venv/Scripts/python.exe -m pytest`（全量，重点 test_main.py）
5. [x] slice-4：routers/jobs.py 拆 jobs_ai.py/jobs_batch.py（路径零变化） → 验证: `backend pytest`（重点 test_routers.py，53 passed；修复测试 fixture 未挂载新路由 + mock 命名空间未跟随迁移的问题）
6. [x] verify 全量：backend pytest 402 passed（项目 venv）+ frontend vitest 24 passed + build 通过
7. [x] 归档：spec 回写（backend 路由组织/任务编排章节、frontend Composable 章节）+ CONTEXT.md 刷新 + 收尾 commit

## 硬规则

- 每步完成即 commit（COMMIT-DISCIPLINE），失败留 [WIP-FAIL] 痕迹
- 不改 prd/design/验收；新想法记 change-backlog.md
- 同一根因失败 2 次换策略，禁第 3 次盲重试
- 后端重构期间禁止引入新的同步阻塞调用

## 收尾记录（2026-08-27）

- 测试环境坑：沙箱默认 Python 缺 pytest-asyncio，36 个异步用例批量假失败；必须用 `backend/venv/Scripts/python.exe -m pytest`（402 全绿）。
- slice-4 测试修复要点：test_routers.py 的 app fixture 需挂载 jobs_ai/jobs_batch 两个新路由；mock 目标从 `routers.jobs.*` 改为 `routers.jobs_ai.*`/`routers.jobs_batch.*`（模块级 import 决定 patch 命名空间）。
- 并行 Edit 同一文件存在竞态覆盖（本次踩过一次，第 3 处编辑被第 4 处回写覆盖），同文件多处修改应串行执行。
