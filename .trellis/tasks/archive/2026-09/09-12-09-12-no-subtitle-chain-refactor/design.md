# 无字幕链路结构修复 + 免费优先 - Design

## 架构：2 个结构单点 + 3 个缺陷修复

### 单点 1：音频获取 `_acquire_audio`（`backend/main.py`）

`_handle_url_input` 内三条"拿到待转录音频"的路径收敛为一个函数，统一登记 `files['audio']`：

```python
async def _acquire_audio(input_url, job_dir, sio, job_id, options, *,
                         title='', platform='', pre_downloaded_audio=None, files=None):
    """获取待转录音频（复用预下载 → 断点续传 → 正常下载），单点登记 files['audio']。
    返回 (file_path, title, platform)。"""
```

- 复用优先：`pre_downloaded_audio`（抽查失败路径）> `_retry` 扫描 job_dir > 按 options 下载三模式
- 不变量「凡是进入转录的音频都登记 files['audio']」只在此处实现（消除 A3 类漏登记）
- 调用方 `_handle_url_input` 只保留「是否需要转录」的决策，不再散落下载分支

### 单点 2：默认引擎策略 `recommended_engine`（`backend/routers/asr.py`）

默认引擎策略从"前端 + 后端链两处实现"收敛为后端一处：

```
free_configured  → 'cloud_free'（免费优先）
cloud_configured → 'cloud'     （免费档不可用但配了付费：用户配置即意愿）
否则             → 'local'     （firered 不参与推荐：避免默认触发 1.1GB 下载）
```

前端 `settingsStore.loadCloudAsrStatus` 只做消费：默认引擎为 'cloud'（未手选状态）时替换为
`recommended_engine`；请求失败仍落 'local'（原兜底语义）。运行时回退链 `_FALLBACK_CHAINS` 不动，
两者方向一致：cloud_free 失败绝不回付费（已有测试锁定）。

### 缺陷修复

| 编号 | 修复 |
|---|---|
| A1 | `transcribe_audio` 首条日志标签改用 `_ENGINE_LABELS`（删临时三态 dict；`_ENGINE_LABELS` 在模块下层定义，运行时查找无碍） |
| A2 | 见单点 2；`settingsStore` 顶部注释同步（免费优先） |
| A3 | 见单点 1；`_retry` 分支同样登记 |
| B1 | 抽查只下载前 90s 片段：`download_video(max_seconds=90)` → `--download-sections *0-90`（加固姿势实测 3.3s/6.9MB；失败自动回退完整下载且可见）。片段放 `job_dir/_subtitle_check/`，避免被断点续传扫描误用；片段不作转录输入（抽查失败时转录走正常完整下载，A3 复用逻辑随之移除） |
| B3 | `main.py` 无字幕/抓取失败日志改中性表述（"转转录（引擎: X）"经 `transcriber.engine_label` 取标签）；`downloader.py` message 去 "Whisper" 写死、平台文案无关化 |

## 契约

`GET /api/asr/status` 响应新增字段（向后兼容，旧前端忽略）：

```json
{ "cloud_configured": true, "free_configured": true, "firered_ready": false,
  "recommended_engine": "cloud_free" }
```

## 数据模型
无变更（无 schema、无迁移）。

## 回滚形态
每逻辑单元独立 commit，逐条 revert；无数据/接口破坏性变更。

## 协作模式
后端先行（结构 + 策略 + 测试），前端随后消费推荐字段（小改 + vitest）。

## 实测验证（2026-09-12）
- `--download-sections` 加固姿势可用：前 10min 片段 3.3s / 6.9MB（research/free-asr-eval.md）
- 免费档 XingChen Ultra 同素材（10min）：21.2s / 零幻觉 / 2109 字（mimo 基线 2077 字）；
  术语同音字错误率高于 mimo（灵龟八法→零规八法 等）→ 结论"日常可用，专业内容开 AI 校正"

## 后续可选优化（本轮不做）
- YouTube/其他平台字幕抽查
