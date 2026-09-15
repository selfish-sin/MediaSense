# ASR 管理模块 - Design

## 架构

```
前端 /models 页 = 「模型管理」（一级 el-tabs，两域明确区分）
├─ LLM 模型        （现有 AI 模型管理，原样移入 tab，标题改为「LLM 模型」）
└─ ASR 模型        AsrPanel.vue（二级 el-radio-group 分段：云端 ASR / 本地 ASR）
      ├─ 云端 ASR  AsrCloudPanel.vue  ─┐
      └─ 本地 ASR  AsrLocalPanel.vue  ─┘─ asrStore.js ─ utils/api.js asrApi
                                                        │
后端 /api/asr ──┬── asr.py        （只读：status / overview）
                └── asr_admin.py  （写：providers CRUD / test / settings / local models）
                        │
                  db/asr_repo.py ─ SQLite: asr_providers（新表）+ app_settings（既有 KV）
                        │
                  services/cloud_asr.py  _provider_cfg()  ← DB 优先，config.py 兜底（唯一读取点）
                  services/local_asr.py   下载状态机 / 就绪 / 删除（唯一模型文件入口）
```

**UI 风格约束（硬要求）**：不引入任何新视觉语言——复用 `App.vue` 的全局类
（`.page-container` / `.app-card` / `.wb-card` / `.row-more-btn` / `.row-more-arrow`）、
Element Plus 既有组件（el-tabs / el-table / el-dialog / el-form / el-tag / el-progress / el-dropdown）
与主题变量（`--app-*` / el 主题变量）。行内操作沿用「主操作 + 更多下拉」模式（防窄窗口列覆盖）。
层次：**LLM 模型 ⊃ ASR 模型**，两域不混排；ASR 域内再小分云端/本地。

**关键不变量（不可破坏）**

1. 引擎值 `cloud / cloud_free / firered / local` 语义冻结（历史 job options 已存）。
2. `_FALLBACK_CHAINS` 不改；**成本只降不升**（付费失败可降免费，免费失败绝不转付费）。
3. `cloud_asr._provider_cfg()` 仍是云端 provider 配置的**唯一读取点**；新增 DB 层后仍从此处读，
   其它模块不得直接碰 `asr_providers` 表。
4. `main.py` 的引擎白名单校验保留（非法值归一 `cloud`）。
5. provider 与引擎槽位解耦：`provider.key` 是配置实体（如 `mimo`/`my-relay`），
   `provider.engine` 是它在回退链里的槽位（`cloud` / `cloud_free`）。槽位可挂多个 provider，
   运行时取「该槽位 enabled 且 sort_order 最小」的那个。

## 契约

前缀 `/api/asr`。

### 只读（`routers/asr.py`）

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/asr/status` | 既有端点，**新增字段** `default_engine`（用户设置的默认引擎，空串=未设置） |
| GET | `/api/asr/overview` | 一次喂满管理页：`providers`（脱敏）+ `local`（本地模型状态）+ `settings` + `recommended_engine` |

### 写（`routers/asr_admin.py`）

| Method | Path | Request | Success | Errors |
|---|---|---|---|---|
| POST | `/providers` | `{key,name,engine,api,endpoint,model,api_key,enabled,concurrency,timeout,remark}` | `{success,message}` | 400 key 重复 / key 非法 / engine 非法 |
| PUT | `/providers/{key}` | 同上（api_key 留空或含 `****` = 保留原值） | `{success,message}` | 404 不存在 |
| DELETE | `/providers/{key}` | — | `{success,message}` | 400 内置不可删 / 404 |
| POST | `/providers/{key}/test` | — | `{success,text,latency_ms,error}` | 404 |
| POST | `/settings` | `{default_engine?, whisper_model?, whisper_device?}` | `{success,settings}` | 400 非法值 |
| POST | `/local/firered/download` | — | `{started:bool,message}` | 409 已在下载 |
| POST | `/local/firered/cancel` | — | `{success,cancel_requested}` | — |
| DELETE | `/local/firered` | — | `{success,freed_bytes}` | 409 下载中禁删 |
| DELETE | `/local/whisper/{size}` | — | `{success,freed_bytes}` | 400 非法档位 / 404 未安装 |

**边界约定**

- 密钥一律脱敏返回（`前4 + **** + 后4`），`key_broken` 标记含 `****` 的污染串。
- `api_key` 为空串 → 视为"不修改"（POST 新增时为空则允许，测试连接会提示未配置）。
- 空列表返回 200 + `providers: []`。
- 下载状态：`{status: idle|downloading|error|done, percent, message, error}`；轮询间隔 2s。

## 数据模型

```sql
CREATE TABLE IF NOT EXISTS asr_providers (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    engine      TEXT NOT NULL DEFAULT 'cloud',  -- cloud | cloud_free（回退链槽位）
    api         TEXT NOT NULL DEFAULT 'chat',   -- chat | multipart（请求姿势）
    endpoint    TEXT NOT NULL DEFAULT '',
    api_key     TEXT DEFAULT '',
    model       TEXT DEFAULT '',
    enabled     INTEGER DEFAULT 1,
    concurrency INTEGER,                        -- 预留：分 provider 并发（本轮不参与运行时）
    timeout     INTEGER,                        -- 预留：分 provider 超时
    is_builtin  INTEGER DEFAULT 0,
    sort_order  INTEGER DEFAULT 100,
    remark      TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`app_settings` 新增键：`asr_default_engine` / `asr_whisper_model` / `asr_whisper_device`。

**播种**：`init_db()` 后调 `db.asr_repo.seed_builtin_providers()`——表为空时从
`config.ASR_CLOUD_*` / `ASR_FREE_*` 生成 `mimo`(engine=cloud, api=chat) 与
`xingchen`(engine=cloud_free, api=multipart) 两条内置记录。用户已有的 config.py Key
一次性迁入 DB（后续以 DB 为准，config.py 只作兜底）。

## 运行时改动（最小面）

- `cloud_asr._provider_cfg(key)`：先查 DB，命中即返回；未命中回退现有 config 属性读取。
  返回结构不变（`enabled/endpoint/api_key/model/api`），新增 `name`。
- `cloud_asr._extract_asr_text(cfg.api, payload)`：由按 provider 名分支改为按**请求姿势**分支
  （`multipart` → `text` 字段；`chat` → `choices[0].message.content`），自定义 provider 才不会错分支。
- `cloud_asr.provider_for_engine(engine)`：槽位 → 首选 provider key（无 → `None`）。
- `transcriber._transcribe_sync`：`provider = cloud_asr.provider_for_engine(engine)`，为空即视为该引擎
  未配置并走回退链（文案含"未配置"）。
- `cloud_asr._rescue_failed_chunks`：免费档 provider 由 `provider_for_engine('cloud_free')` 得到，
  与主 provider 相同则跳过该级（原 `!= 'xingchen'` 的语义等价推广）。
- `routers/asr._recommended_engine`：**用户设置的 `asr_default_engine` 优先**（存在且该引擎可用时采用），
  否则维持免费优先自动推荐。

## 本地模型管理（`services/local_asr.py` 扩展）

```python
_download_state  # {status, percent, message, error}，线程安全，模块级单例
start_model_download() -> bool          # 起 daemon 线程跑 _ensure_model_ready；已在跑返回 False
cancel_model_download() -> bool         # 置取消事件（_download_file 的 cancel_check 消费）
get_download_state() -> dict
remove_model_files() -> dict            # rmtree 三件套目录 + 标点目录 + vad 文件，返回 freed_bytes
model_file_status() -> dict             # 五个文件逐项（存在/大小）+ 总占用
```

Whisper 侧（`transcriber.py` 已有 `LOCAL_MODEL_DIRS` / `_model_complete`）由
`router` 直接复用：逐档返回 `ready / path / bytes / files`；删除走 `shutil.rmtree`。

## 回滚形态

- 代码：按切片逐个 revert。
- 数据：`asr_providers` 为新增表，旧代码不读它；`app_settings` 三个新键旧代码不读。
- 行为：DB 表清空后 `_provider_cfg` 回退 config.py，与改动前完全一致。

## 协作模式

垂直切片（后端先行 2 片 → 前端 1 片 → 串联验收 1 片），每片独立可验证。

## 切片清单

- slice-01 云端 provider 管理（表/仓库/CRUD/测试连接/配置读取改 DB 优先）| API 5 | 后端
- slice-02 本地模型管理（Whisper 清单与删除 / FireRed 下载·取消·删除 / 默认引擎与设备）| API 6 | 后端
- slice-03 模型管理页两域 Tab 化（LLM / ASR）+ ASR 域内云端·本地小分面板 | 前端 1 页 + 3 组件 + 1 store
- 串联：默认引擎链路贯通 + 浏览器 E2E
