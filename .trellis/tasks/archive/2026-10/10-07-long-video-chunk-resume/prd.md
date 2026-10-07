# long-video-chunk-resume - PRD

## 需求
长视频转录（分片，>20 分钟）中途失败/取消后，重试必须从头重跑所有分片。
现有重试只能复用完整音频/完整转录文件；分片级进度不落盘。

## 方案
分片检查点：每片转录完成立即写 `{stem}_chunkNNN.transcript.json`（offset+segments），
- 恢复：`_transcribe_chunked` 逐片先查检查点（offset 校验一致才复用），命中跳过识别；
- `bypass_cache=true` 时忽略检查点（用户勾"忽略缓存"就是要全新识别，不能复用旧结果）；
- 全部完成后写合并 SRT/TXT 并清理检查点；`_split_audio_chunks` 复用已存在的有效 WAV 片免重切；
- bypass_cache 参数沿 `transcribe_audio → _transcribe_sync → _transcribe_by_engine → _transcribe_chunked` 串接。

## 验收标准
- [ ] 每片完成即有检查点文件；中途失败后恢复只重跑缺失片
- [ ] 检查点 offset 与本次切分不一致时忽略（防错位）
- [ ] bypass_cache=true 不读检查点但照常写
- [ ] 完成后检查点清理干净
- [ ] 单测覆盖：检查点写入/读取/offset 校验/清理/bypass/恢复跳过
- [ ] verify 全绿

## 回滚
revert 提交；检查点是新增旁路文件，无 schema 变更。
