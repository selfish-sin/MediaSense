# 质量优化：实时推送/日志修复/巨型文件拆分 - Implementation Plan

## 执行清单

1. [ ] slice-1：HomeView 日志增量回退 + catch 提示 + 死代码删除 → 验证: `cd frontend && npm run test && npm run build`
2. [ ] slice-2：后端 batch_progress emit + 前端 socket 订阅（含降级轮询） → 验证: `backend pytest` + `frontend npm run test && npm run build` + 新增后端 emit 单测
3. [ ] slice-5：补 composable 测试 → 拆 useBackendLogs/useBatchProgress/useJobRestore → HomeView 瘦身 → 验证: `frontend npm run test && npm run build`
4. [ ] slice-3：_process_job_impl 拆策略函数（信号量/取消语义不动） → 验证: `backend venv/Scripts/python.exe -m pytest`（全量，重点 test_main.py）
5. [ ] slice-4：routers/jobs.py 拆 jobs_ai.py/jobs_batch.py（路径零变化） → 验证: `backend pytest`（重点 test_routers.py）
6. [ ] verify 全量：backend pytest + frontend vitest + build + 重建 static 资产
7. [ ] 归档：spec 回写 + CONTEXT.md 刷新 + 收尾 commit

## 硬规则

- 每步完成即 commit（COMMIT-DISCIPLINE），失败留 [WIP-FAIL] 痕迹
- 不改 prd/design/验收；新想法记 change-backlog.md
- 同一根因失败 2 次换策略，禁第 3 次盲重试
- 后端重构期间禁止引入新的同步阻塞调用
