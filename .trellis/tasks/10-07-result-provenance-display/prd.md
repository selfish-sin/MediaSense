# result-provenance-display - PRD

## 需求
我要解决什么问题：结果页看不出这份转录结果怎么来的——是平台字幕还是识别的、用了哪个引擎、
是否复用缓存、是否发生降级。用户据此判断要不要换引擎重跑。

## 具体例子
给一个 B站 URL，期望结果页显示「来源：B站平台字幕（内容抽查通过）」；
给一个本地音频，期望显示「来源：语音识别 · 云端 XingChen（免费）· 全新识别」，
若请求 cloud 实际用了 cloud_free，显示「已从 云端 MiMo 降级到 云端 XingChen（免费）」。

## 验收标准
- [ ] jobs 表新增 `transcript_meta` JSON 列（迁移 ALTER TABLE，旧任务该字段为 null）
- [ ] `transcribe_audio` 经 `info` 出参回填：实际引擎/请求引擎/缓存命中（hit/miss/bypassed）/降级标记
- [ ] 四个输入策略函数返回 meta：text_input / subtitle_file / platform_subtitle（含 subtitle_from、
      subtitle_check=passed|skipped|unavailable）/ transcription（含断点续传 resume=true）
- [ ] `GET /api/jobs/{id}` 与列表响应经 JobResponse 暴露 `transcript_meta`
- [ ] 前端 `TranscriptProvenance.vue` 组件（首页结果面板 + 结果详情页共用），用户语言展示，一行一句
- [ ] openapi.json 重新导出
- [ ] 后端/前端回归测试覆盖：引擎降级、缓存命中、平台字幕来源、断点续传复用、repo 序列化往返

## 不做项
- 不改转录链任何既有行为（只加观测）
- 不做逐引擎分段耗时统计
- 历史任务不回填 meta（显示为「无来源记录」或不渲染）

## 影响范围
- backend: transcriber.py / main.py / db/database.py / db/job_repo.py / models/schemas.py
- frontend: 新组件 + HomeResultPanel / ResultView 挂载
- contracts/openapi.json

## 风险与回滚
- 风险等级：M1（跨模块但方案清晰，无行为变更）
- 不可逆操作：无（纯新增列/字段）
- 回滚方式：revert 提交即可；新列留存无副作用

## Design（一页）

### 契约
`transcript_meta` JSON（jobs 列 + JobResponse 字段）：
```json
{
  "source": "platform_subtitle | subtitle_file | text_input | transcription",
  "subtitle_from": "bilibili | douyin | youtube",
  "subtitle_check": "passed | skipped | unavailable",
  "engine": "cloud_free",
  "engine_label": "云端 XingChen（免费）",
  "requested_engine": "cloud",
  "cache": "hit | miss | bypassed",
  "resume": true,
  "degraded": true
}
```

### 数据流
1. `transcribe_audio(..., info=None)`：命中缓存 → info={engine:缓存引擎, cache:'hit', degraded:False}；
   正常完成 → info={engine:execution_state 实际值, requested_engine, cache:'miss'|'bypassed', degraded}
2. main.py 四个 `_handle_*` 返回值 6→7 元组（+meta），`_process_job_impl` Step 1 后
   `update_job(job_id, transcript_meta=meta)`
3. job_repo 两张 JSON 字段表同步（update_job 序列化 + _row_to_dict 反序列化）
4. 前端 `TranscriptProvenance.vue` 消费 meta 渲染；meta 为 null 不渲染（历史任务）

### 回滚形态
revert 单提交；列与字段向后兼容。
