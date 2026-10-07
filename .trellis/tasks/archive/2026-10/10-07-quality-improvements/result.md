# 质量改进验收（2026-10-07）

风险 M1，范围来自用户「那你都做吧」确认质量报告全部建议。
批准 design.md 与计划提交 bb9bce4 相同（归一化 SHA256：
`85d445387c33952d2073a03d8a3e36f30e11c5c8701eec9be11bf4d40dbfcb44`）。

## 需求 → 实现 → 验证

| 验收 | 实现 | 证据 |
|---|---|---|
| ASR 相同配置复用，换模型配置重新识别 | ai_cache.transcription_key + transcriber._transcription_cache_key，asr_v2 | TestTranscriptionIdentity：档位/provider key/端点/model/api/旧键；初始 7 条修复前失败 |
| 故障恢复继续尝试首选 | _transcribe_sync execution_state + transcribe_audio 写入守卫 | 整体降级 local 后 cloud_free 恢复回归 |
| 识别中配置变化不污染旧缓存 | 写入前重新核对身份 | 追加回归修复前失败、修复后通过；最新相关四组 243 passed |
| 忽略缓存跳过读仍更新 | 已有 AI/ASR 真实入口回归保留 | TestAiBypassRefresh / TestTranscribeAudioBypass；实际引擎/身份变化守卫仍成立 |
| Python 门禁/告警 | verify ruff + pytest，协程替身 close，RuntimeWarning 失败 | 清理 132 基线；test_verify.py 两项失败传播回归 |
| 可复现依赖和启动 | 67 运行 / 80 开发锁、显式 cryptography、start.bat、lifespan | 干净安装、pip check、已装版本匹配与启动导入；TestClient 组合 ASGI 真启动 |
| 新机器/CI 可运行 | Windows CI，同一 verify 入口，两个假 ASR 槽位 | 默认示例配置曾 8 failed / 970 passed，修 fixture 后完整 979 passed |
| 记录/索引正确 | 历史两任务归档、codegraph.json、规范/CONTEXT | 保留提交及历史验收，不重新声明外部媒体认证；索引已重建与同步 |

## 最终检查

- Windows x64，Python 3.13.14，Node.js 24.15.0。
- 项目 `verify.py all`：退出 0；ruff 通过、979 backend passed、269 frontend passed（22 文件）、eslint/build 通过。
- 干净锁定环境使用默认无密钥示例配置：979 passed，无测试告警（没有替换本机 config.py）。
- pip check 无冲突，所有锁定版本匹配；CI YAML 解析与关键字段通过。
- 隔离 localhost HTTP：初始化临时 DB、队列、OpenAPI、SPA、仅本地 CORS，通过；服务正常退出，临时目录清理完成。
- design 无漂移；task 上下文清单校验通过；CONTEXT 185 行（≤300），UTF-8 无损；前端产物无差异。
- 第三方 @vueuse 构建注释提示不阻断构建。未执行真实付费请求、媒体识别准确率横评或远程 CI。

## 单元提交

- 46a66d5：ASR v2 模型/配置身份和整体降级缓存。
- a0bb81e：Python 静态门禁、协程告警、验证退出码。
- 008abb9：lifespan 与启动恢复回归。
- 7c85a75：历史任务归档与索引排除。
- 38bf11d：运行/开发依赖锁、启动与 Windows CI。
- df3f3fe：云端测试不依赖本机配置。
- 144f3cf：识别中身份变化跳过缓存写入。
- 打包配置同步移除 collect_submodules('bilibili_api')，AST 语法核验通过；未重建安装包。
  CodeGraph 不解析 .spec，explore 未覆盖该配置，按配置文件读取当前源；没有新增运行依赖。
- 本文件/规范/CONTEXT/报告随归档独立提交。

本轮只做本地提交，未 push、发布或重启真实服务；业务改动需下次重启生效。
