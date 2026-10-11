# 10-11-platform-subtitle-cache - Design

## 架构

复用现有缓存池 `services/ai_cache.py` + `ai_cache` 表（`op` 字段无约束），新增第三类
`op='subtitle'`。读写全部收口在 `ai_cache.py` 三个新函数，`main.py` 只在两点调用：

```
main._handle_url_input
  ├─ [前置] _subtitle_cache_key(platform_id, language)  → 命中? → 写回 job_dir + meta[cached] → 跳过三平台分支
  ├─ 平台分支（bilibili / douyin / youtube）——原样保留
  │     └─ [抽查通过 / 抓取成功] → store_subtitle(...)（未验证不写）
  └─ if not srt_path → 转录（原样）
```

## 契约（services/ai_cache.py 新增）

```python
subtitle_key(normalized_id: str, language: Optional[str]) -> str
    # make_key('subtitle_v1', normalized_id, language or '')

lookup_subtitle(normalized_id, language, output_dir: Path) -> Optional[dict]
    # 命中：把缓存 SRT/TXT 写回 output_dir（文件名用缓存 meta['base']，兜底 'subtitle'）
    # 返回 {'srt_path','txt_path','title','subtitle_from','created_at','seg_count'}；未命中 None

store_subtitle(normalized_id, language, srt_path, txt_path, *, title='', subtitle_from='', seg_count=0) -> bool
    # content=SRT 全文；meta={txt, seg_count, base, title, subtitle_from}
    # 走既有 cache_put（只缓存成功且非空；超限/失败返回 False 不阻断）
```

## 键的规范化（normalized_id）

复用 `routers/jobs.py::normalize_url`（已是 `bilibili:BVxxx` / `youtube:<id>` / `douyin:<id>`
单点），并对 B站 b23.tv 短链用 `main._extract_bvid()` 的结果补正为 `bilibili:<BV>`——
避免「短链 vs 标准链接」被当成两个视频而 miss。抖音短链沿用 `douyin:short:<code>`。

## 数据模型

无 schema 变更。写入已有 `ai_cache` 行：`op='subtitle'`、`model=subtitle_from`、
`content=SRT 全文`、`meta=json{txt,seg_count,base,title,subtitle_from}`。

## 命中路径（main._handle_url_input）

在三个平台分支**之前**插入（受 `force_transcribe` / `bypass_cache` / `AI_CACHE_ENABLED` 三重门控）：

1. 算 `normalized_id`；为空（无法识别平台）则跳过缓存。
2. `lookup_subtitle(...)` 命中 → 设置 `srt_path/txt_path/title`、
   `meta={'source':'platform_subtitle','subtitle_from':...,'subtitle_check':'cached','cache':'hit'}`，
   emit「⚡ 命中平台字幕缓存（X 月 X 日获取）——跳过探测与抽查」，`update_job`。
3. 三个平台分支加 `if srt_path: 跳过` 守卫（命中后不再探测）。

## 写入路径

- B站：`meta['subtitle_check'] == 'passed'` 时写。
- 抖音：同上。
- YouTube：抓取成功即写（无抽查环节）。
- 任何 `unavailable` / 抽查未通过 / 抓取失败 → 不写。

## 回滚形态

`git revert` 本轮 commit；或 `YT_AI_CACHE_ENABLED=0` 整体关闭。
缓存条目为加速项，残留条目不影响正确性（读不到即回现状）。

## 协作模式

后端单侧（纯 backend + 单测）。无前端改动（日志/结果来源沿用既有 `transcript_meta` 通道）。

## 已知取舍

- 字幕条目与 AI 条目共享 `AI_CACHE_MAX_ENTRIES=500` 全局上限：极端情况下字幕条目会挤掉
  旧 AI 缓存（后果仅 AI 重跑一次调用，秒级）。本轮不引入独立容量池，保持 `_prune` 单点简单。
- 命中不重新抽查：风险与「抽查只比对开头 90s」的既有能力同档，不引入新风险类别，
  仅把「当次正确」延伸为「长期复用」，由「忽略缓存」开关兜底。
