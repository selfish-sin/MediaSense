# 抖音解析模块可行性调研

> 2026-10-05 | 调研性质，未实现。结论：**可行，推荐借鉴 f2 库做主链路**；本文为后续建 task 的 research 依据。
> 数据来源：GitHub API 实时值（2026-10-05）、yt-dlp 源码、本项目真机实测（PATH python yt-dlp 2026.07.04）。

## 一句话结论

加抖音模块可行且成本低：**主链路引入开源库 f2（`Johnserf-Seed/f2`，git 装 master）拿无水印直链 + 元数据，字幕白拿 detail API 的 AI 字幕轨（`cla_info.caption_infos`），yt-dlp 降级兜底**。预估 M1 级、约 3-5 天。纯自研签名逆向（方案A）与 Docker 旁路服务（方案C）均不推荐。

## 1. 本项目现状（实测）

- `downloader.py:265` `PLATFORM_CONFIG` 已登记 `douyin.com`（标签"抖音"，不走代理），`main.py:_handle_url_input` 无 B站分支命中即走通用链路：`extract_youtube_subtitles`（必失败）→ `_acquire_audio` → yt-dlp 下载 → ASR 转录。
- **实测（2026-10-05）**：yt-dlp（venv 2026.06.09 / PATH python 2026.07.04 均同）对 `douyin.com/video/<id>` 游客态直接报
  `ERROR: [Douyin] ...: Fresh cookies (not necessarily logged in) are needed`。
  即**当前"支持抖音"名存实亡，任务会在下载步整单失败**。
- 单发 ttwid cookie（公开注册接口 `ttwid.bytedance.com/ttwid/union/register/` 实测可拿）**不足以**喂活 yt-dlp——它要求真实浏览器访问产生的完整 cookie 集。
- 接入缺口：`sanitize_input_url` 只补 `b23.tv`，不补 `v.douyin.com` 裸短链；`iesdouyin.com` 分享链不在 `PLATFORM_CONFIG`（会被判"未知平台"且 need_proxy=True 走代理）。
- 可复用件：`services/bilibili_cookies.py`（统一 cookie 存储模式）、`downloader.py:~1149`（Netscape cookie 文件写给 yt-dlp 的现成姿势）、`verify_subtitle_against_audio`（字幕内容抽查，平台无关，可直接复用于抖音 AI 字幕防错位）、前端零改动（URL 输入自由文本，平台名来自后端）。

## 2. yt-dlp 路线评估（结论：只配兜底）

- `DouyinIE` 在 `yt_dlp/extractor/tiktok.py`（≈L1371），只认 `douyin.com/video/<数字id>`，调 web detail 接口，**自身不生成签名**，全靠 cookie；源码内 TODO 承认验证挑战未实现。
- 主 issue #9667（2024-04 起，2026-09 仍活跃）："即使 --cookies-from-browser 也持续 403"；短链支持 #11500 被 not_planned 关闭；用户主页 #2120 挂了 5 年未实现。
- 亮点：其源码证实 detail 响应含 AI 字幕轨并解析（`cla_info` → webvtt/srt）。

## 3. 可借鉴开源项目对比（star 为 2026-10-05 实时值）

| 项目 | Stars | 形态 | 内置签名 | 关键结论 |
|---|---|---|---|---|
| Evil0ctal/Douyin_TikTok_Download_API | 20,468 | 独立 FastAPI 服务（v5 需 Docker+Postgres+Redis+无头浏览器身份池） | a_bogus/X-Bogus/X-Gnarly/X-Dynosaur 纯Py + 浏览器兜底 | 更新最快（v5.1.3，2026-10-02，天级迭代）、短链直传、批量最全；**对单机工作站是数量级超配，留作未来批量升级项** |
| JoeanAmier/TikTokDownloader | 16,504 | CLI/TUI，非库 | **已声明放弃维护签名算法** | 排除 |
| **Johnserf-Seed/f2**（注意已从 Johnserf-Stack 迁移，旧 URL 404） | 2,678 | **唯一可 import 的纯 Python 异步库** | `ABogusManager` + msToken 本地生成 | master 当天仍有 commit；**PyPI 版 0.0.1.7 停在 2024-12-31，必须 git 装 master**；`fetch_one_video`/`fetch_user_post_videos`/`fetch_user_mix_videos` |
| jiji262/douyin-downloader | 12,188 | CLI/桌面，非库 | 内置 | 形态不合 |
| davidteather/TikTok-Api | 6,674 | 库 | — | **不支持抖音**（仅国际版 TikTok） |

集成先例：`yinzhy5/douyin-to-md` = f2 下载音频 + faster-whisper，与本项目形态几乎相同，可直接照抄姿势。

## 4. 签名/风控/字幕/批量要点

- **游客可拿无水印**：游客 cookie（ttwid 核心，可自动注册）+ 正确 a_bogus 签名即可；登录 cookie 仅粉丝/私密内容需要。风控表现：高频（约 3 分钟一次持续请求）触发滑块；cookie 失效是接口失效第一大原因。
- **签名变动节奏**：X-Bogus(2022)→a_bogus(2023)→X-Gnarly/X-Dynosaur(2025)。跟进速度：Evil0ctal > f2(master) > jiji262 > yt-dlp（基本不管抖音）。纯自研（方案A）= 自己扛 JSVMP 字节码逆向，2-4 周起步且签名一变全断，否决。
- **字幕（好消息）**：detail API 的 `video.cla_info.caption_infos[]` 就是抖音 AI 自动字幕，直接给 webvtt/srt 下载 URL（yt-dlp 源码级证据）。注意 **f2/Evil0ctal 都不透出该字段**，需自己从 f2 返回的原始数据里抠。部分视频没开 AI 字幕 → 回退现有转录链（"字幕优先、缺转录"策略天然兼容）。App"作品文稿"无公开 API（未找到公开数据）。
- **批量潜力**：f2 主页/合集/点赞/收藏接口现成；yt-dlp 完全无。近期单视频即可，批量留给后续。

## 5. 推荐集成方案（供 design 参考）

**主链路 f2（git master）+ 兜底 yt-dlp + 字幕直取 cla_info。**

改动面（预估）：
1. 新增 `backend/services/douyin.py`：f2 封装（fetch detail → 标题/时长/作者/play_url/captions）、cla_info → SRT 写盘、直链下载（httpx 或交 yt-dlp）。
2. `downloader.sanitize_input_url`：补 `v.douyin.com/xxx` 裸短链 + iesdouyin 分享链归一（短链需 302 展开取 video id）。
3. `PLATFORM_CONFIG` 加 `iesdouyin.com`。
4. `main._handle_url_input` 加 `is_douyin` 分支（镜像 is_bilibili）：先试 AI 字幕 → `verify_subtitle_against_audio` 抽查复用 → 失败回退转录。
5. 依赖：`requirements.txt` + **start.bat 探测清单同步**（服务跑 PATH python 非 venv，两环境都要装）；f2 走 git URL 安装。
6. cookie：MVP 可先赌 f2 游客身份可用（PoC 验证）；不行则按 `bilibili_cookies.py` 模式加 `douyin_cookies.json` + 简单导入 UI（抖音无便捷扫码 API，实用形态是浏览器 cookie 粘贴）。

## 6. 合规边界

抖音 ToS 禁止未授权爬取/批量下载；判例（抖音诉刷宝等）针对的是批量搬运/商用替代。本项目=本地个人工作站、单视频、不分发、控制请求频率，实际风险低。**红线：不得把下载内容或解析能力对外服务化/再分发。**

## 7. 落地建议

- 等级建议 M1（跨模块但方案清晰：douyin.py + main 分支 + sanitize/PLATFORM + 测试，前端零改动）。
- 第一步 PoC（半天）：临时 venv git 装 f2，真机跑通 fetch_one_video（含短链展开、cla_info 抽取、无水印直链下载 10s 片段），验证通过再建 task 进 Open→Design。
- 风险预案：f2 配置生成较繁琐（dy.yaml）、API 形态可能在 master 漂移——PoC 就地确认；若 f2 游客态被风控拦，退路是 Evil0ctal 的签名模块思路或旁路服务。
