# 实施与验证

- [ ] ASR 身份/实际引擎：补真实函数回归，定向 ASR/cache/管理测试，commit。
- [ ] Python 静态门禁/测试告警：修真实基线，运行 ruff 与 backend 验证，commit。
- [ ] 依赖锁/启动兼容/CI：干净安装与依赖检查，全量 verify，commit。
- [ ] 过时任务/索引/规范：核对证据、归档、文档回写，commit。
- [ ] 本地 API 冒烟与最终工作树检查；本轮不 push。

CodeGraph：启动状态 up to date，但 explore ai_cache.py / 精确函数返回无关符号并包含 static/archive，
已尝试 graph 优先；未覆盖的目标使用当前源码。此前已验证关键调用链由 transcribe_audio 统一读写缓存，
_transcribe_sync 是引擎回退单点，cloud_asr._provider_cfg 是配置读取单点。
