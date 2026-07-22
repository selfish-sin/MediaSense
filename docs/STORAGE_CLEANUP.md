# 安全存储清理

清理入口是 `backend/scripts/cleanup_storage.py`。默认模式只扫描，不创建锁、审计或隔离文件，也不删除现有数据。

## 前端工作台

应用侧栏的“存储管理”进入 `/storage`。页面提供策略设置、dry-run 预览、候选与受保护路径、逐项执行进度、操作日志和审计记录。

- 所有 `/api/storage-cleanup/*` 接口只接受 localhost 请求。
- 每次预览生成新的短时 `plan_id`，旧计划立即失效，计划默认 15 分钟过期。
- 执行前必须没有 `pending/downloading/transcribing/preview/summarizing` 任务。
- 执行弹窗必须准确输入 `DELETE`，后端同时复核 `plan_id` 和计划有效期。
- 页面执行在后端工作线程中进行；状态接口持续返回逐项结果与回收空间。
- URL 音频默认不参与清理，必须在策略中每次显式开启。

前端可以在后端运行时安全操作，因为执行由同一进程协调并检查活动任务。命令行 apply 仍要求先停止后端，避免两个进程竞争。

## 常用命令

在 `backend/` 目录执行：

```powershell
.\venv\Scripts\python.exe -B scripts\cleanup_storage.py
.\venv\Scripts\python.exe -B scripts\cleanup_storage.py --json
```

执行模式必须同时提供 `--apply --confirm DELETE`，并且后端监听端口必须已停止：

```powershell
.\venv\Scripts\python.exe -B scripts\cleanup_storage.py --apply --confirm DELETE
```

URL 音频默认永久保留。只有额外提供 `--prune-url-audio` 才会把满足保留期、已有字幕且未设置保留标记的已完成任务音频列入计划。

## 默认规则

| 类别 | 默认条件 | 执行动作 |
|---|---|---|
| `staged_unreferenced` | 无任务引用的上传超过 72 小时 | 删除文件 |
| `staged_completed_duplicate` | 已完成文件任务存在同名同大小的任务目录副本 | 先原子更新数据库路径，再删除暂存副本 |
| `orphan_job_dir` | 目录名符合任务 ID、数据库无记录且超过 14 天 | 移入隔离区 |
| `expired_export` | 导出文件超过 7 天 | 删除文件 |
| `expired_url_audio` | 显式开启媒体清理、任务已完成、有字幕且超过 7 天 | 删除音频并更新 `files` 元数据 |
| `quarantine_expired` | 隔离内容超过 30 天 | 删除隔离内容 |

保留期和预算可通过 `YT_CLEANUP_*` 环境变量或 CLI 参数覆盖。媒体清理不能只靠环境变量开启，必须每次显式传入命令行开关。

## 安全边界

- 每个候选必须位于预定义根目录内，根目录本身永不删除。
- 符号链接、Windows 重解析点和包含此类节点的目录树永不清理。
- 执行前重新校验路径、大小和修改时间；变化的候选记为 `skipped`。
- 单次执行受项目数和字节数预算限制，超预算时在任何删除前整体拒绝。
- `backend/data/.cleanup.lock` 防止并发执行。
- `backend/data/cleanup_audit.jsonl` 记录开始、逐项结果和结束汇总。
- 孤儿任务目录先进入 `backend/data/.cleanup_quarantine/`，在隔离保留期内可人工检查。

任务删除 API 也遵守相同生命周期原则：活动任务必须先取消并等待 worker 退出；单项、批量、按状态和全部清空都会处理对应任务目录。
