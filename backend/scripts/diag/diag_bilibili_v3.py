"""
B站字幕诊断 v3 — 针对豆包视频做完整测试
"""
import sys
import json
import re
import httpx
from urllib.parse import urlencode

sys.path.insert(0, '.')
from services.bilibili_cookies import load_cookies
from services.downloader import _build_wbi_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Origin': 'https://www.bilibili.com',
}

cookies_dict = load_cookies() or {}
if cookies_dict:
    cookie_str = '; '.join(f'{k}={v}' for k, v in cookies_dict.items())
    HEADERS['Cookie'] = cookie_str
    print(f"✅ 已加载 {len(cookies_dict)} 个cookie")

# 测试两个视频
test_videos = [
    ("BV1wb7Y65EnF", "豆包，帮帮小咕嘎", "豆包"),
    ("BV1yy736XEi2", "[精神现象学13]", "哲学13"),
    ("BV1Go7q6rEbR", "[精神现象学8]", "哲学8"),
]

with httpx.Client(timeout=15.0, headers=HEADERS) as client:
    for bvid, expected_title, label in test_videos:
        print(f"\n{'='*70}")
        print(f"测试视频: {label} (bvid={bvid})")
        print(f"期望标题: {expected_title}")
        print(f"{'='*70}")

        # view API 拿 aid+cid
        view_url = _build_wbi_url(
            "https://api.bilibili.com/x/web-interface/wbi/view",
            {"bvid": bvid}
        )
        resp = client.get(view_url)
        view_data = resp.json().get('data', {})
        aid = view_data.get('aid')
        cid = view_data.get('cid')
        title = view_data.get('title', '')
        print(f"实际标题: {title}")
        print(f"aid={aid}, cid={cid}")

        # 调用 player/v2 多次（看是否每次返回不同字幕）
        for attempt in range(3):
            print(f"\n  --- 第{attempt+1}次调用 player/v2 ---")
            params = {
                "aid": aid,
                "cid": cid,
                "part": 1,
                "dm_img_str": "V2ViR0VuZGFyOlxyZXR1cm4gTmV3cyBLZXkgUGFnZSBMaXN0Ig==",
                "dm_cover_img_str": "",
                "dm_img_list": "[]",
                "dm_img_crypto": "",
                "web_location": "1315875",
            }
            url_p = f"https://api.bilibili.com/x/player/v2?{urlencode(params)}"
            resp = client.get(url_p)
            result = resp.json()
            subs = result.get('data', {}).get('subtitle', {}).get('subtitles', [])
            print(f"  字幕数量: {len(subs)}")
            for s in subs:
                sub_url = s.get('subtitle_url', '')
                print(f"    lan={s.get('lan')}, url={sub_url[:80]}")

                # 下载字幕内容
                if sub_url:
                    if sub_url.startswith('//'):
                        sub_url = 'https:' + sub_url
                    elif sub_url.startswith('/'):
                        sub_url = 'https://api.bilibili.com' + sub_url
                    try:
                        resp_sub = client.get(sub_url)
                        if resp_sub.status_code == 200:
                            body = resp_sub.json().get('body', [])
                            if body:
                                first_text = body[0].get('content', '') if isinstance(body[0], dict) else ''
                                print(f"    字幕首句: {first_text}")
                                # 取前3条内容
                                for i, item in enumerate(body[:3]):
                                    if isinstance(item, dict):
                                        print(f"      [{item.get('from',0):.1f}s] {item.get('content','')}")
                            else:
                                print(f"    字幕内容为空")
                    except Exception as e:
                        print(f"    下载异常: {e}")
