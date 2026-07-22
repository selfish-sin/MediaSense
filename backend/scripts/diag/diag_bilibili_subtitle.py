"""
B站字幕诊断脚本
排查：为什么 player/v2 API 返回的字幕URL对应的是另一个视频的字幕

用法：
    cd D:/projects/youtube_tool_v2/backend
    python diag_bilibili_subtitle.py "https://www.bilibili.com/video/BV1Go7q6rEbR"

输出：
    1. view API 返回的视频标题、cid、pages（多P信息）
    2. player/v2 API 返回的字幕列表（lan、lan_doc、subtitle_url）
    3. 字幕URL下载后的内容前5条
    4. 对比：字幕内容是否与视频标题匹配
"""
import sys
import json
import re
import hashlib
import time
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

# 复用项目的 cookie
sys.path.insert(0, '.')
from services.bilibili_cookies import load_cookies
from services.downloader import _get_wbi_keys, _compute_wbi_sign, _build_wbi_url

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 52, 1, 44, 30, 22, 25,
    24, 13, 6, 21, 40, 37, 34, 41, 17, 7, 36, 39, 11, 26,
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Origin': 'https://www.bilibili.com',
}


def get_subtitle_content(client, sub_url):
    """下载字幕JSON并返回前5条内容"""
    if sub_url.startswith('//'):
        sub_url = 'https:' + sub_url
    elif sub_url.startswith('/'):
        sub_url = 'https://api.bilibili.com' + sub_url
    elif not sub_url.startswith('http'):
        sub_url = 'https://' + sub_url

    try:
        resp = client.get(sub_url)
        if resp.status_code != 200:
            return f"[下载失败 HTTP {resp.status_code}]"
        sub_json = resp.json()
        if isinstance(sub_json, dict):
            body = sub_json.get('body', [])
        elif isinstance(sub_json, list):
            body = sub_json
        else:
            body = []

        if not body:
            return "[字幕内容为空]"

        lines = []
        for i, item in enumerate(body[:5]):
            if isinstance(item, dict):
                start = item.get('from', 0)
                text = item.get('content', '')
                lines.append(f"  [{start:.1f}s] {text}")
        return "\n".join(lines)
    except Exception as e:
        return f"[下载异常: {e}]"


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/BV1Go7q6rEbR"

    # 提取 BV 号
    match = re.search(r'bilibili\.com/video/(BV[A-Za-z0-9]+)', url)
    if not match:
        print(f"❌ 无法从URL提取BV号: {url}")
        return
    bvid = match.group(1)

    # 解析 ?p= 参数
    page = 1
    p_match = re.search(r'[?&]p=(\d+)', url)
    if p_match:
        page = int(p_match.group(1))

    print(f"=" * 70)
    print(f"诊断B站字幕问题")
    print(f"=" * 70)
    print(f"输入URL: {url}")
    print(f"提取BV号: {bvid}")
    print(f"分P参数: p={page}")
    print()

    cookies_dict = load_cookies() or {}
    if cookies_dict:
        cookie_str = '; '.join(f'{k}={v}' for k, v in cookies_dict.items())
        HEADERS['Cookie'] = cookie_str
        print(f"✅ 已加载 {len(cookies_dict)} 个cookie: {list(cookies_dict.keys())}")
    else:
        print(f"⚠️ 未加载cookie（未登录B站）")
    print()

    with httpx.Client(timeout=15.0, headers=HEADERS) as client:

        # ===== Step 1: view API =====
        print(f"{'='*70}")
        print(f"Step 1: view API（获取视频信息和cid）")
        print(f"{'='*70}")

        view_url = _build_wbi_url(
            "https://api.bilibili.com/x/web-interface/wbi/view",
            {"bvid": bvid}
        )
        print(f"请求URL: {view_url[:100]}...")

        resp = client.get(view_url)
        print(f"HTTP状态: {resp.status_code}")

        view_data = resp.json()
        if view_data.get('code') != 0:
            print(f"❌ view API返回错误: code={view_data.get('code')}, message={view_data.get('message')}")
            return

        data = view_data['data']
        title = data.get('title', '')
        cid = data.get('cid')
        aid = data.get('aid')
        pages = data.get('pages', [])

        print(f"视频标题: {title}")
        print(f"aid: {aid}")
        print(f"data.cid (第一P): {cid}")
        print(f"分P数量: {len(pages)}")
        if pages:
            for i, p in enumerate(pages[:5]):  # 只显示前5P
                print(f"  P{i+1}: cid={p.get('cid')}, part={p.get('part','')}")

        # 如果是多P，取对应P的cid
        actual_cid = cid
        if page > 1 and pages and len(pages) >= page:
            actual_cid = pages[page - 1].get('cid')
            print(f"\n✅ 多P视频: 使用第{page}P的cid={actual_cid}")
        elif page > 1:
            print(f"\n⚠️ 该视频只有{len(pages)}P，使用第1P的cid={cid}")

        print()

        # ===== Step 2: player/v2 API（3种调用方式对比） =====
        print(f"{'='*70}")
        print(f"Step 2: player/v2 API（对比3种调用方式）")
        print(f"{'='*70}")

        # 方式A：只传 bvid+cid（当前代码的做法）
        print(f"\n--- 方式A: 只传 bvid+cid（当前代码做法）---")
        url_a = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={actual_cid}"
        resp_a = client.get(url_a)
        data_a = resp_a.json().get('data', {})
        subs_a = data_a.get('subtitle', {}).get('subtitles', [])
        print(f"URL: {url_a}")
        print(f"字幕数量: {len(subs_a)}")
        for s in subs_a:
            print(f"  lan={s.get('lan')}, lan_doc={s.get('lan_doc')}, url={s.get('subtitle_url','')[:80]}")

        # 方式B：带完整播放器参数（模拟B站播放器）
        print(f"\n--- 方式B: 带完整播放器参数（模拟B站播放器）---")
        params_b = {
            "aid": aid,
            "cid": actual_cid,
            "bvid": bvid,
            "part": page,
            "dm_img_str": "V2ViR0VuZGFyOlxyZXR1cm4gTmV3cyBLZXkgUGFnZSBMaXN0Ig==",
            "dm_img_crypto": "dXNlciBkZWZhdWx0",
            "dm_cover_img_str": "",
            "dm_img_list": "[]",
            "web_location": "1315875",
            "from": "web",
            "platform": "pc",
            "high_quality": "1",
        }
        url_b = f"https://api.bilibili.com/x/player/v2?{urlencode(params_b)}"
        resp_b = client.get(url_b)
        data_b = resp_b.json().get('data', {})
        subs_b = data_b.get('subtitle', {}).get('subtitles', [])
        print(f"字幕数量: {len(subs_b)}")
        for s in subs_b:
            print(f"  lan={s.get('lan')}, lan_doc={s.get('lan_doc')}, url={s.get('subtitle_url','')[:80]}")

        # 方式C：带 web_location + from_av（另一种常见调用）
        print(f"\n--- 方式C: 带 web_location 参数 ---")
        params_c = {
            "aid": aid,
            "cid": actual_cid,
            "bvid": bvid,
            "web_location": "1315875",
        }
        url_c = f"https://api.bilibili.com/x/player/v2?{urlencode(params_c)}"
        resp_c = client.get(url_c)
        data_c = resp_c.json().get('data', {})
        subs_c = data_c.get('subtitle', {}).get('subtitles', [])
        print(f"字幕数量: {len(subs_c)}")
        for s in subs_c:
            print(f"  lan={s.get('lan')}, lan_doc={s.get('lan_doc')}, url={s.get('subtitle_url','')[:80]}")

        print()

        # ===== Step 3: 下载字幕内容对比 =====
        print(f"{'='*70}")
        print(f"Step 3: 下载字幕内容对比（看是不是同一个字幕）")
        print(f"{'='*70}")

        all_subs = [('A', s) for s in subs_a] + [('B', s) for s in subs_b] + [('C', s) for s in subs_c]

        seen_urls = set()
        for label, s in all_subs:
            sub_url = s.get('subtitle_url', '')
            if sub_url in seen_urls:
                print(f"\n方式{label}: lan={s.get('lan')} — URL与之前相同，跳过")
                continue
            seen_urls.add(sub_url)

            print(f"\n方式{label}: lan={s.get('lan')}, lan_doc={s.get('lan_doc')}")
            print(f"  字幕URL: {sub_url[:100]}...")
            content = get_subtitle_content(client, sub_url)
            print(f"  字幕内容前5条:")
            print(content)

        print()

        # ===== Step 4: 结论 =====
        print(f"{'='*70}")
        print(f"Step 4: 诊断结论")
        print(f"{'='*70}")
        print(f"视频标题: {title}")
        print(f"如果字幕内容与视频标题不符 → B站API返回了错误的字幕URL")
        print(f"如果3种方式返回的字幕URL相同 → 是B站数据问题，我们无能为力")
        print(f"如果3种方式返回的字幕URL不同 → 需要切换到正确的方式调用")

        # ===== 额外: 检查 aid+cid 是否匹配 =====
        print(f"\n--- 额外检查: aid+cid 对应的视频 ---")
        check_url = f"https://api.bilibili.com/x/web-interface/view?aid={aid}"
        resp_check = client.get(check_url)
        check_data = resp_check.json().get('data', {})
        check_title = check_data.get('title', '')
        check_cid = check_data.get('cid')
        print(f"用 aid={aid} 查询视频:")
        print(f"  标题: {check_title}")
        print(f"  cid: {check_cid}")
        if check_title == title:
            print(f"  ✅ aid+cid 对应的视频标题一致")
        else:
            print(f"  ❌ aid+cid 对应的视频标题不一致！可能存在关联错误")


if __name__ == '__main__':
    main()
