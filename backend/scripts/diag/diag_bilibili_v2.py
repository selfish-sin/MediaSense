"""
B站字幕诊断 v2 — 对比4种数据来源
关键测试：直接抓网页HTML，从 __INITIAL_STATE__ 提取字幕信息
这是B站播放器自己用的数据来源，绝对正确
"""
import sys
import json
import re
import httpx

sys.path.insert(0, '.')
from services.bilibili_cookies import load_cookies
from services.downloader import _build_wbi_url

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

url = "https://www.bilibili.com/video/BV1Go7q6rEbR"
cookies_dict = load_cookies() or {}
if cookies_dict:
    cookie_str = '; '.join(f'{k}={v}' for k, v in cookies_dict.items())
    HEADERS['Cookie'] = cookie_str
    print(f"✅ 已加载 {len(cookies_dict)} 个cookie")

match = re.search(r'(BV[A-Za-z0-9]+)', url)
bvid = match.group(1)
print(f"BV号: {bvid}")

with httpx.Client(timeout=15.0, headers=HEADERS) as client:

    # ===== 来源1: 直接抓网页HTML =====
    print(f"\n{'='*70}")
    print("来源1: 直接抓网页HTML，提取 __INITIAL_STATE__")
    print(f"{'='*70}")

    resp = client.get(url)
    print(f"HTTP状态: {resp.status_code}")
    html = resp.text

    # 提取 __INITIAL_STATE__
    state_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>', html, re.DOTALL)
    if state_match:
        try:
            state = json.loads(state_match.group(1))
            print("✅ 找到 __INITIAL_STATE__")

            # 导航到字幕信息
            video_data = state.get('videoData', {})
            title = video_data.get('title', '')
            cid = video_data.get('cid')
            aid = video_data.get('aid')
            print(f"  标题: {title}")
            print(f"  cid: {cid}, aid: {aid}")

            # subtitle 信息
            sub_info = video_data.get('subtitle', {})
            print(f"  subtitle字段: {json.dumps(sub_info, ensure_ascii=False)[:300]}")

            # 也可能有 subtitle_list 在别处
            sub_list = sub_info.get('subtitles', [])
            print(f"  字幕数量: {len(sub_list)}")
            for s in sub_list:
                print(f"    lan={s.get('lan')}, lan_doc={s.get('lan_doc')}, subtitle_url={s.get('subtitle_url','')[:100]}")
        except Exception as e:
            print(f"❌ 解析__INITIAL_STATE__失败: {e}")
    else:
        print("❌ 未找到 __INITIAL_STATE__")

    # 也找 playinfo
    playinfo_match = re.search(r'window\.__playinfo__\s*=\s*(\{.*?\})\s*</script>', html, re.DOTALL)
    if playinfo_match:
        try:
            playinfo = json.loads(playinfo_match.group(1))
            print("\n✅ 找到 __playinfo__")
            # 字幕可能在 data.subtitle
            sub = playinfo.get('data', {}).get('subtitle', {})
            subs = sub.get('subtitles', [])
            print(f"  __playinfo__里的字幕数量: {len(subs)}")
            for s in subs:
                print(f"    lan={s.get('lan')}, lan_doc={s.get('lan_doc')}, subtitle_url={s.get('subtitle_url','')[:100]}")
        except Exception as e:
            print(f"❌ 解析__playinfo__失败: {e}")

    # ===== 来源2: player/wbi/v2 带 wbi 签名 =====
    print(f"\n{'='*70}")
    print("来源2: player/wbi/v2 带 wbi 签名")
    print(f"{'='*70}")

    # 先拿 cid
    view_url = _build_wbi_url(
        "https://api.bilibili.com/x/web-interface/wbi/view",
        {"bvid": bvid}
    )
    resp = client.get(view_url)
    view_data = resp.json().get('data', {})
    cid = view_data.get('cid')
    aid = view_data.get('aid')
    title = view_data.get('title', '')
    print(f"cid={cid}, aid={aid}, title={title}")

    # player/wbi/v2
    player_wbi_url = _build_wbi_url(
        "https://api.bilibili.com/x/player/wbi/v2",
        {"bvid": bvid, "cid": cid}
    )
    print(f"player/wbi/v2 URL: {player_wbi_url[:120]}...")
    try:
        resp = client.get(player_wbi_url)
        print(f"HTTP状态: {resp.status_code}")
        result = resp.json()
        print(f"code: {result.get('code')}, message: {result.get('message')}")

        if result.get('code') == 0:
            data = result.get('data', {})
            sub = data.get('subtitle', {})
            subs = sub.get('subtitles', [])
            print(f"字幕数量: {len(subs)}")
            for s in subs:
                sub_url = s.get('subtitle_url', '')
                print(f"  lan={s.get('lan')}, lan_doc={s.get('lan_doc')}")
                print(f"  subtitle_url: {sub_url[:120]}")

                # 下载字幕内容
                if sub_url:
                    if sub_url.startswith('//'):
                        sub_url = 'https:' + sub_url
                    elif sub_url.startswith('/'):
                        sub_url = 'https://api.bilibili.com' + sub_url
                    try:
                        resp_sub = client.get(sub_url)
                        if resp_sub.status_code == 200:
                            sub_json = resp_sub.json()
                            body = sub_json.get('body', []) if isinstance(sub_json, dict) else sub_json
                            print("  字幕内容前3条:")
                            for item in body[:3]:
                                if isinstance(item, dict):
                                    print(f"    [{item.get('from',0):.1f}s] {item.get('content','')}")
                        else:
                            print(f"  字幕下载失败: HTTP {resp_sub.status_code}")
                    except Exception as e:
                        print(f"  字幕下载异常: {e}")
    except Exception as e:
        print(f"❌ player/wbi/v2 失败: {e}")

    # ===== 来源3: player/v2 带 aid（不用bvid）+ 完整浏览器参数 =====
    print(f"\n{'='*70}")
    print("来源3: player/v2 带 aid + 完整参数")
    print(f"{'='*70}")

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
    from urllib.parse import urlencode
    url3 = f"https://api.bilibili.com/x/player/v2?{urlencode(params)}"
    try:
        resp = client.get(url3)
        result = resp.json()
        if result.get('code') == 0:
            subs = result.get('data', {}).get('subtitle', {}).get('subtitles', [])
            print(f"字幕数量: {len(subs)}")
            for s in subs:
                sub_url = s.get('subtitle_url', '')
                print(f"  lan={s.get('lan')}, subtitle_url: {sub_url[:120]}")
                if sub_url:
                    if sub_url.startswith('//'):
                        sub_url = 'https:' + sub_url
                    resp_sub = client.get(sub_url)
                    if resp_sub.status_code == 200:
                        body = resp_sub.json().get('body', [])
                        print("  内容前3条:")
                        for item in body[:3]:
                            if isinstance(item, dict):
                                print(f"    [{item.get('from',0):.1f}s] {item.get('content','')}")
    except Exception as e:
        print(f"❌: {e}")

    # ===== 来源4: aid+cid 直接查 view API（确认 aid/cid 正确）=====
    print(f"\n{'='*70}")
    print("来源4: aid+cid 反查 view API")
    print(f"{'='*70}")
    url4 = f"https://api.bilibili.com/x/web-interface/view?aid={aid}"
    resp = client.get(url4)
    data4 = resp.json().get('data', {})
    print(f"aid={aid} → 标题: {data4.get('title')}")
    print(f"aid={aid} → cid: {data4.get('cid')}")
    if data4.get('title') == title:
        print("✅ aid/cid 对应视频正确")
