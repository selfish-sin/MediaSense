# 实施与验证

- [x] ASR 身份/实际引擎：7 条新回归修复前全失败；修复后 cache/transcriber/cloud/管理共 242 条通过。
  v2 键纳入模型档位、设备/平台、provider 白名单及模型文件签名；整条降级结果不写成首选缓存。
- [x] Python 静态门禁/测试告警：132 项基线清理；全量 E/F/W（E501 除外）接入 verify。
  无效目标拒绝执行；超时替身 close 协程；RuntimeWarning/未回收异常作失败。
  ruff 全绿，977 backend passed，仅剩 2 条启动事件弃用警告（下一单元处理）。
- [ ] 依赖锁/启动兼容/CI：干净安装与依赖检查，全量 verify，commit。
  启动迁移已完成：lifespan 保留 init_db → 中断任务标记失败 → 过期转换文件清理。
  真实 socket_app ASGI 入口验证恢复状态、任务/队列/OpenAPI；30 定向测试通过，无告警。
- [ ] 过时任务/索引/规范：核对证据、归档、文档回写，commit。
  两个历史 planning 任务已补 result 并按 Trellis 归档，active 指针未改。
  CodeGraph 官方随包 README 确认 root codegraph.json.exclude 支持已提交目录；
  排除 backend/static/ 与 .trellis/tasks/archive/ 后重建：节点 9574→5132，DB 44.27→15.41 MB。
  transcription_key 和 _transcription_cache_key 已能定位，归档研究脚本不再入图；模糊查询仍需核对路径。
- [ ] 本地 API 冒烟与最终工作树检查；本轮不 push。

CodeGraph：启动状态 up to date，但 explore ai_cache.py / 精确函数返回无关符号并包含 static/archive，
已尝试 graph 优先；未覆盖的目标使用当前源码。此前已验证关键调用链由 transcribe_audio 统一读写缓存，
_transcribe_sync 是引擎回退单点，cloud_asr._provider_cfg 是配置读取单点。
