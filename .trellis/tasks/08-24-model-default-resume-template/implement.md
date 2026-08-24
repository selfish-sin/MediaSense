# Implement — 模型默认 / 失败重试 / 新增模板优化

## 执行顺序（每步后跑对应测试）

1. **后端存储层**：`backend/db/database.py` 加 `app_settings` 表；`backend/db/model_repo.py` 加 `get_default_model_key()` / `set_default_model()`。
   → 更新 `backend/tests/test_model_repo.py`（fixture 建表 + 用例），跑该文件。
2. **后端默认模型接口**：`backend/routers/models.py` — GET default 字段、POST /default、端点补全 helper（add/update 幂等）、测试接口 choices 加固。
   → 加 `test_routers.py` 用例，跑该文件。
3. **根因修复**：`backend/services/ai_processor.py:128` choices 空数组防护。
   → `test_ai_processor.py` 加流式回归用例，跑该文件。
4. **resummarize 放宽**：`backend/routers/jobs.py` — 状态白名单 + 哨兵检查；同步批量提交默认模型回退。
   → `test_routers.py` 加 failed 重总结成功/哨兵不写盘用例。
5. **默认回退点**：`routers/translate.py`、`db/job_repo.py` create_job、`main.py:376` 改用 `get_default_model_key()`。
   → 跑 `test_routers.py` / `test_job_repo.py` / `test_main.py` 相关回归。
6. **前端 API/Store**：`frontend/src/utils/api.js` 加 `setDefault`；`stores/modelStore.js` 加 `setDefaultModel`。
7. **ModelsView**：默认按钮 + 标签、服务商预设、一键添加、自动派生、端点补全。
8. **HomeView / ResummarizeDialog**：默认模型初始化、watcher 尊重默认、弹窗兜底。
9. **ResultView**：失败横幅 + 重新总结可用条件。
10. **验证**：
    - `cd backend && python -m pytest tests/ -q`（全量）
    - `cd frontend && npx vitest run`（全量）
    - `cd frontend && npm run build`（重建 static/）
    - 手工核对 ModelsView/失败重总结关键路径（可用浏览器）。

## 验证命令
```powershell
cd backend; python -m pytest tests/ -q
cd frontend; npx vitest run; npm run build
```

## 评审门
- 默认模型设置后刷新/重启不丢；未设置回退 config。
- 失败任务重总结不产生下载/转录日志，成功后状态回 completed。
- 模型添加弹窗：预设一键填端点；拉列表一键添加直接保存。

## 回滚点
- 所有改动独立成 commit；若 resummarize 放宽有问题，只回退 jobs.py 状态白名单一行即可。
