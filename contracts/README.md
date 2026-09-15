# contracts

前后端契约的落盘快照目录（AGENTS.md §11.4）。

- `openapi.json` — 后端 FastAPI 自动生成的 OpenAPI 3.1 契约（76 个路径）。
  权威来源是 `backend/main.py` + `backend/routers/` 的 pydantic schema，本文件只是快照。
- 在线交互文档（服务运行时）：`http://localhost:7001/docs`

## 更新方式

**后端 API 变更后必须重新导出并随改动提交**：

```powershell
cd backend
.\venv\Scripts\python.exe scripts\export_openapi.py
```

脚本会重新生成 `contracts/openapi.json` 并打印端点数量。若数量与预期不符，说明路由没挂上或导出失败（不要手改本文件）。
