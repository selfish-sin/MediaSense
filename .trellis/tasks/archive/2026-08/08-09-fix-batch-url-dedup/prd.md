# 修复批量URL被去重逻辑全量跳过

## Goal

批量提交 URL 时，已处理过的 URL 会被 `find_job_by_url` 无条件判为重复并跳过，导致整批 URL 全部被 skip、`clean_urls` 为空、后端抛 400「没有有效的URL（全部为空或重复）」。应让批量路径与单 URL 路径行为对齐：支持强制重跑已处理过的 URL。

## 需求

我要解决什么问题：批量 URL 提交对「已存在历史任务的 URL」不可用，而单 URL 提交可用——两条路径行为不一致。

## 根因（已查清，2026-08-09 诊断）

- 单 URL 路径：`HomeView.vue:513` 发 `force_new: true` → `submit_job()`（`jobs.py:199-210`）跳过 `find_job_by_url`。
- 批量路径：`HomeView.vue:528-582` 走独立 `/api/jobs/batch`（`jobs.py:718`），前端**从不发** `force_new`，后端 `batch_submit` **也不读** `force_new`，而是无条件执行 `find_job_by_url(u)`（`jobs.py:791`）。
- 只要历史里存在同 URL 的任务且状态 ∈ {pending, completed, preview, downloading, transcribing, summarizing}，该 URL 即被判重复、`skipped += 1`（`jobs.py:792-801`）。
- 当全部 URL 都被判重复 → `clean_urls` 为空 → `raise HTTPException(400, "没有有效的URL（全部为空或重复）")`（`jobs.py:804-805`）。
- 次要因素：`extract_urls()` 的 `_URL_RE`（`jobs.py:26`）要求 `https?://`，裸 `b23.tv/xxx` 或 `BV1xxxxx` 被静默丢弃（`jobs.py:778-781`），而单 URL 路径的 `process_job` 会经 `sanitize_input_url()`（`main.py:382`）补全——批量路径在创建任务前就拒绝了。

## 具体例子

给 `/api/jobs/batch` 输入一个**之前已 completed** 的 YouTube URL，期望：能重新创建任务并处理。
实际：返回 400「没有有效的URL（全部为空或重复）」，前端 `ElMessage.error`（`HomeView.vue:581`）。

## 验收标准

- [ ] 批量提交一个已存在历史任务的 URL，默认行为仍跳过（保留去重价值），但有方式强制重跑
- [ ] 前端批量提交带 `force_new`（默认 true，与单 URL 一致）；后端 `batch_submit` 读取 `force_new`，为 true 时跳过 `find_job_by_url` 跨历史去重
- [ ] 批内去重（`seen_norm`/`seen_raw`，`jobs.py:785-788`）仍生效——同一批里重复的 URL 仍只创建一个任务
- [ ] 裸短链/BV（无 `https?://` 前缀）在批量路径也能被处理（调用 `sanitize_input_url` 或放宽 `extract_urls`）
- [ ] `duplicates` 返回值仍正确（force_new=false 时仍返回被跳过的历史任务信息）
- [ ] 新增/更新测试：覆盖「已存在 URL + force_new=true 应创建任务」「已存在 URL + force_new=false 应跳过」「裸 BV 应能提交」
- [ ] backend pytest 全绿（含修掉 DEFAULT_MODEL 漂移的 3 个 WIP-FAIL，或与本任务解耦单独修）

## 不做项

- 不改单 URL 路径的行为（它已正确）
- 不改 `find_job_by_url` 的匹配逻辑本身（只改批量路径是否调用它）
- 不改批量并发上限（仍受 `MAX_CONCURRENT_JOBS=2` 限制）

## 影响范围

- `backend/routers/jobs.py`（`batch_submit` 718-836）
- `frontend/src/views/HomeView.vue`（批量提交 528-582）
- 可能：`backend/routers/jobs.py` 的 `extract_urls`/`_URL_RE` 或在批量路径接入 `sanitize_input_url`
- `backend/tests/test_routers.py`（批量测试用例）

## 风险与回滚

- 风险等级：M0（单模块、改法明确、有单 URL 路径作参照）
- 不可逆操作：无
- 回滚方式：revert 该 commit；force_new 默认值改回不发送即可恢复原去重行为
