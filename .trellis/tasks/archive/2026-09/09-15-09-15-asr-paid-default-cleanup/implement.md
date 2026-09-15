# ASR 付费档默认值污染清理 - Implementation Plan

## 不变量（本次要立的硬规则）

> **付费档（engine 槽位 `cloud`）只能由用户显式选择触发；任何默认值、兜底、一次性自动调用一律不得落到它。**

## 执行清单

1. [ ] `services/cloud_asr.py`
   - `preferred_provider()` 语义收紧：**只返回免费槽位（`cloud_free`）的可用 provider**，
     免费档不可用 → 返回 `None`（调用方回退本地）。付费档不再作为自动兜底。
     → 验证: `tests/test_cloud_asr.py` 改用例（原 `test_paid_when_free_unavailable` 反转为 `None`）
   - `transcribe_file(provider: Optional[str] = None)`：None → `preferred_provider()`；
     仍为 None → 抛 `CloudASRConfigError`（可行动文案），不再默认 `'mimo'`。
   - `transcribe_audio_cloud(provider: Optional[str] = None)`：同上。
   - 模块 docstring 与 §错误语义 同步新策略。
2. [ ] `services/downloader.py::verify_subtitle_against_audio`
   - 注释改述新语义（免费档；不可用 → 本地 Whisper，不再提"免费档不可用才用付费档"）。
3. [ ] `services/transcriber.py`
   - `_transcribe_sync(..., engine: str = 'cloud_free')`、`transcribe_audio(..., engine='cloud_free')`。
   - `_fallback_chain`：未知引擎 → `_FALLBACK_CHAINS['cloud_free']`（永不回付费）。
   - 注释/docstring 同步（"非法值按 cloud" → "非法值按 cloud_free，付费档只能显式选择"）。
4. [ ] `main.py`
   - `transcribe_engine = options.get('transcribe_engine')`；
     非法/缺省 → `routers.asr.recommended_engine()`（免费优先推荐，绝不隐式付费）。
   - `_handle_media_file` / `_handle_url_input` 形参默认值改 `'cloud_free'`。
5. [ ] 前端 `stores/settingsStore.js` + `components/HomeAdvancedOptions.vue`
   - 新增 `engineTouched`（同 `modelTouched` 思路）：占位值改为安全默认；
     `loadCloudAsrStatus` 只在**用户未触碰**时消费 `recommended_engine`；
     `@touch` 事件由引擎下拉触发。
6. [ ] 测试
   - 后端：新不变量用例（默认 provider 免费优先 / 无免费档时 DefaultError 而非付费 /
     main 缺省引擎落到推荐 / 未知 engine 回免费链）。
   - 前端：settingsStore 用例适配（占位 + engineTouched）。
7. [ ] 验证：`backend/venv/Scripts/python.exe -m pytest`（须在 `backend/` 下跑）；
   前端 `npm run lint && npm test && npm run build`（或 `python verify.py`）。
8. [ ] 文档：`spec/backend/transcription-chain.md`（§1 抽查 / §2 默认引擎 / §7 坑点 / §6 参数表）
   + `CONTEXT.md` 当前状态。
9. [ ] 清理临时脚本（`backend/_tmp_asr_eval.py`、`backend/_tmp_asr_probe.py`）与
   `backend/data/_asr_eval/` 评测残留。

## 本次横评结论（写入 spec 供选型参考）

同素材（BV1Av4y1A796 p41 柴胡，前 90s，中医术语密集），基准 = B站字幕头 231 字：

| 引擎 | 耗时 | 字数 | 相似度 | 中医术语表现 |
|---|---|---|---|---|
| 云端 XingChen（免费） | 10.3s | 203 | 0.837 | 辛凉解表药/伞形科 对；「狭叶柴胡」→夏叶柴胡（同音错） |
| 本地 FireRedASR2-AED | 44.2s | 255 | **0.932** | 术语几乎全对，文本最完整 |
| Whisper base（本地） | 20.0s | 223 | 0.713 | 柴胡→柴糊、辛凉解表药→新两节表药、伞形科→散心克植物 |
| Whisper medium（本地） | 37.4s | 241 | 0.866 | 伞形科→陕西科植物、辛凉→心良节 |

要点：**质量最高的是本地 FireRed（免费、离线）**；免费云端最快；Whisper base 是链路里最弱一环。
付费 MiMo 未参与本次横评（欠费 402，不可用）。

## 切片清单

- slice-01: 后端自动路径去付费化（cloud_asr + downloader + transcriber + main） | 无 API/schema 变化
- slice-02: 前端引擎占位去付费化 | 依赖 slice-01 的语义约定
- slice-03: 测试 + 文档回写
