# MediaSense 智能多媒体处理工作站

MediaSense 是一个本地运行的视频、音频、字幕和文本处理工作站。它可以抓取平台字幕或使用 Whisper 转录，再通过可配置模型生成总结、翻译、标签和思维导图。

当前主线不包含旧版文档 OCR、书籍处理和万能格式转换器。历史设计资料保留在仓库中，但不代表当前可用功能。

## 当前功能

- YouTube、Bilibili 等平台 URL 处理，优先使用可用字幕并回退 Whisper。
- 本地音频、视频、SRT、VTT、TXT 和 Markdown 文件输入。
- 文本直接总结。
- 转录预览确认、取消、断点续传和从头重试。
- 模块化总结维度、自定义提示词、教材模式和深度研究模式。
- 总结版本、标签、思维导图、追问聊天和多格式导出。
- SQLite 历史记录、批量任务和 Socket.IO 实时进度。

## 技术栈

- 前端：Vue 3、Vite、Element Plus、Pinia、Socket.IO Client、markmap。
- 后端：FastAPI、python-socketio、SQLite WAL、yt-dlp、faster-whisper、httpx。
- 数据目录：`backend/data/`。

## 安装

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd ..\frontend
npm install
```

复制配置模板，并通过环境变量或模型管理页面配置 API Key：

```powershell
Copy-Item backend\config.example.py backend\config.py
$env:ZHIPU_API_KEY = "your-api-key"
```

不要把真实 API Key、cookie、数据库、下载文件或模型文件提交到仓库。

## 启动

后端：

```powershell
cd backend
python main.py
```

默认访问地址是 `http://localhost:7001`，实际端口以 `backend/config.py` 为准。

前端开发模式：

```powershell
cd frontend
npm run dev
```

生产构建会直接更新后端静态目录：

```powershell
cd frontend
npm run build
```

## 测试

```powershell
cd backend
pip install -r requirements-dev.txt
pytest

cd ..\frontend
npm run test
npm run build
```

## 存储清理

应用侧栏提供“存储管理”工作台，可设置规则、预览候选、观察逐项进度与日志，并查看审计记录。执行前需要输入 `DELETE`，后端只允许 localhost、最新短时计划且没有活动任务时开始。

离线安全清理脚本默认只做 dry-run：

```powershell
cd backend
.\venv\Scripts\python.exe -B scripts\cleanup_storage.py
```

执行模式需要后端已停止，并同时提供 `--apply --confirm DELETE`。URL 音频清理还需要单独的 `--prune-url-audio` 开关。规则、审计和隔离说明见 [docs/STORAGE_CLEANUP.md](docs/STORAGE_CLEANUP.md)。

## 当前页面


- `/`：任务工作台。
- `/result/:jobId`：任务结果和追问。
- `/history`：历史任务与批量操作。
- `/models`：模型配置。
- `/templates`：提示词模板和模块覆盖。

## License

[MIT](LICENSE)
