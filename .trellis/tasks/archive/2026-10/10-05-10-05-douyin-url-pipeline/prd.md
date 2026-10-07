# 10-05-douyin-url-pipeline

## Goal
抖音 URL 能被自动识别并进入既有"下载→转录→总结"流程（B站/YT 现状不动）。用户近期目标：处理"跃说中医"（李跃进，经方扶阳学派）的视频。

## Requirements
- 输入形态：`douyin.com/video/<id>`、`user/self?modal_id=<id>`、`iesdouyin.com/share/video/<id>`、`v.douyin.com/xxx` 短链（含裸短链补全）
- 游客链路（无登录）：ttwid 铸造 + a_bogus 签名 → detail 元数据 → play_addr 直链下载音频
- 有 AI 字幕（cla_info）时字幕优先（含内容抽查），无则转录（FireRed/云端既有回退链）
- 前端零改动；B站/YouTube 分支互斥零影响

## 已验证的技术事实（2026-10-05 PoC，真机）
- ttwid 注册接口（bytedance）+ a_bogus 签名（vendor f2 的 abogus.py，SM3 用项目已有 cryptography 替代 gmssl）→ detail 接口游客返回完整数据（含用户真实视频 7689342659723673201 实测）
- play_addr 直链可下载（206 + mp4 magic）；短链 302 展开 → iesdouyin/share/video/<id>
- AI 字幕（cla_info.caption_infos）游客态多条实测为空 → 字幕主要靠转录，属已知边界
- yt-dlp Douyin extractor 需浏览器完整 cookie 且 2026 年仍频繁 403 → 不采用

## Acceptance Criteria
- [ ] 用户真实视频 7689342659723673201 走完 提交→下载→转录→总结 全链
- [ ] 四种输入形态均可识别；裸 `v.douyin.com/xxx` 自动补全
- [ ] B站任务行为零变化（回归）
- [ ] pytest + vitest + lint + build 全绿

## Notes
- 不做：抖音扫码登录/账号 cookie、主页批量、yt-dlp 兜底、其它平台
- 风险：签名/风控变动 → vendor 文件可从 f2 master 重新同步；M1 级，分支互斥可整单元 revert
