# 质量优化：实时推送/日志修复/巨型文件拆分 - Design

## 架构

四个改动方向，彼此独立可回滚：

### 1. 批量进度 Socket.IO 推送（slice-2）

```
后端：process_job 任务终态（done/cancelled/failed）时，若 job 属于某 batch，
      emit socket 事件 batch_progress {batch_id, summary}
前端：socket.js 新增 onBatchProgress 辅助；HomeView（后续 slice-5 后为
      useBatchProgress composable）订阅更新 batchResult.summary，
      仅在 socket 断开/重连时兜底 HTTP 拉取一次。
```

- 复用现有 `socket_handlers.py` 的 emit 基础设施与 `/ws/socket.io` 通道
- 事件负载直接复用 `GET /api/jobs/batch/{id}` 的 summary 结构（单一事实源：`get_batch_status`）
- 轮询保留为降级路径：socket `connect_error` / `disconnect` 时启动低频（10s）轮询，恢复后停止

### 2. 日志增量修复（slice-1）

- `fetchLogs()`：`delta > 200`（超出单次拉取上限）时回退 `loadAllHistory()` 全量重载，不再静默丢弃
- `catch {}` 改为累计失败计数，≥3 次连续失败时 ElMessage.warning 一次（避免每次抖动刷屏），成功时重置计数

### 3. `_process_job_impl` 拆分（slice-3）

```
main.py 保留：process_job（信号量包装）+ _process_job_impl（读配置、目录、统一收尾）
新增 main.py 内模块级策略函数（不新建文件，避免 import 循环风险）：
  _handle_text_input(...)      # input_type == 'text' + .txt/.md 文本文件
  _handle_subtitle_file(...)   # .srt/.vtt
  _handle_media_file(...)      # 音视频文件上传 → 转录
  _handle_url_input(...)       # URL：B站字幕（含抽查）/ YT 字幕 / 下载+转录
  _extract_bvid(url)            # BV 号解析（消除两段重复）
```

- 各策略函数返回统一结构 `(srt_path, txt_path, seg_count, title, platform, files)`
- 信号量/取消/sem_state 语义不动，只搬移纯逻辑
- 断点续传（_retry 扫描已有文件）留在 `_handle_url_input` 内

### 4. routers/jobs.py 按域拆分（slice-4）

```
routers/jobs.py        → 保留 core：upload/submit/list/get/delete/clear/cancel/
                          batch-cancel/batch-delete/batch-disk-info/retry/edit-result
routers/jobs_ai.py     → summarize/summary-history/generate-tags/mindmap/tags(PUT)
routers/jobs_batch.py  → batch 提交 + batch 状态 + search/transcript
```

- 全部挂同一 `/api/jobs` 前缀，对外路径零变化；main.py include_router 补两行
- 共享依赖（DB、start_job 引用）通过模块导入，不复制逻辑

### 5. HomeView 拆 composable（slice-5）

```
frontend/src/composables/
  useBackendLogs.js    # fetchLogs/loadAllHistory/clearLogs/定时器/失败提示
  useBatchProgress.js  # batchResult/socket 订阅/降级轮询
  useJobRestore.js     # rerun/restore 路由参数处理 + applyJobConfig
```

- 先补 vitest：useBatchProgress 的 socket 更新/降级轮询/清理、useBackendLogs 的增量回退逻辑（mock api + socket）
- HomeView 模板中的引用同步更新，删 L187-192 死代码及其模板引用

## 契约

- 新增 socket 事件：`batch_progress`（负载 = `GET /api/jobs/batch/{id}` 的 summary 对象 + batch_id）
- HTTP API 路径、请求、响应：零变化
- 前端无新依赖

## 数据模型

无变化。

## 回滚形态

逐切片独立 commit；任一切片出问题 `git revert <该切片 commit>` 即可，切片间无 schema/契约耦合（slice-5 的 composable 依赖 slice-2 的 socket 事件，但 socket 订阅缺失时降级轮询兜底）。

## 切片清单（M2）

- slice-1: 前端日志增量修复 + 静默吞错 + 死代码清理 | 无 API 变化 | 依赖无
- slice-2: 批量进度 batch_progress 推送（backend emit + 前端订阅） | socket 事件 +1 | 依赖无
- slice-3: _process_job_impl 策略函数拆分 | 内部重构 | 依赖无（建议最后做后端重构，单独验证）
- slice-4: routers/jobs.py 按域拆分 | 内部重构 | 依赖无
- slice-5: HomeView composable 拆分（先补测试） | 依赖 slice-2 的 socket 事件

执行顺序：1 → 2 → 5 → 3 → 4（先做前端小改快速见效，后端大重构放后独立验证）
