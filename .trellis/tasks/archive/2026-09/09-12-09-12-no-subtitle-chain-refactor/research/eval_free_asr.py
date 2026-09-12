"""免费档（XingChen Ultra）同素材质量评测（研究脚本，不入运行路径）。

素材：BV1bH4y1r75q 前 10 分钟（与 archive/09-12-asr-vendor-compare 同素材）。
策略：优先用带完整浏览器指纹（UA/buvid/cookie）的 yt-dlp --download-sections 只取
      前 600s——顺带验证该姿势对 B站 的可用性（B1 优化可行性）；失败回退项目
      download_video 整段下载再 ffmpeg 切。
      → cloud_asr.transcribe_audio_cloud(provider='xingchen')
      → 统计条数/字数/术语/假名幻觉，与 mimo 报告对比。
产出：free-eval-output.txt（转录全文）。
"""
import asyncio
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

TASK_DIR = Path(__file__).resolve().parent
ROOT = TASK_DIR.parents[3]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))

URL = 'https://www.bilibili.com/video/BV1bH4y1r75q'
OUT = Path(tempfile.gettempdir()) / 'free_asr_eval'
OUT.mkdir(exist_ok=True)


def _bili_ytdlp_args():
    """与 downloader 相同的 B站 加固姿势（完整 UA + buvid + cookie）。"""
    from services.bilibili_cookies import load_cookies
    from services.downloader import FULL_CHROME_UA, _ensure_buvid, DATA_DIR

    cookies = load_cookies() or {}
    cookies.update(_ensure_buvid())
    cookie_file = DATA_DIR / '.bilibili_cookies.txt'
    lines = ['# Netscape HTTP Cookie File']
    for name, value in cookies.items():
        if name == 'refresh_token':
            continue
        lines.append(f'.bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}')
    cookie_file.write_text('\n'.join(lines), encoding='utf-8')
    return [
        '--add-header', 'Origin:https://www.bilibili.com',
        '--add-header', 'Referer:https://www.bilibili.com',
        '--add-header', f'User-Agent:{FULL_CHROME_UA}',
        '--cookies', str(cookie_file),
        '--no-check-certificate', '--no-playlist',
        '--ffmpeg-location', str(ROOT / 'bin'),
    ]


def download_clip_sections(seconds: int = 600):
    """尝试 --download-sections（加固姿势）；失败返回 None。"""
    for old in OUT.glob('clip.*'):
        old.unlink(missing_ok=True)
    args = _bili_ytdlp_args() + [
        '-f', 'bestaudio[ext=m4a]/bestaudio',
        '--download-sections', f'*0-{seconds}',
        '-o', str(OUT / 'clip.%(ext)s'), URL,
    ]
    r = subprocess.run([sys.executable, '-m', 'yt_dlp'] + args,
                       capture_output=True, timeout=300)
    if r.returncode != 0:
        tail = r.stderr.decode('utf-8', 'ignore')[-300:]
        print('[download-sections] 失败:', tail.replace('\n', ' '))
        return None
    files = sorted(OUT.glob('clip.*'), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


async def download_full_and_cut(seconds: int = 600) -> Path:
    """回退：项目 download_video 整段下载 + ffmpeg 切前 N 秒。"""
    from services.downloader import download_video
    from services.transcriber import _FFMPEG_BIN

    audio, _title, _platform = await download_video(URL, OUT, only_audio=True)
    src = Path(audio)
    print(f'[download-full] {src.name} {src.stat().st_size // 1024} KB')
    cut = OUT / 'sample600.wav'
    subprocess.run(
        [_FFMPEG_BIN, '-y', '-v', 'quiet', '-i', str(src), '-t', str(seconds),
         '-acodec', 'pcm_s16le', str(cut)],
        timeout=300, check=False)
    if not cut.exists() or cut.stat().st_size <= 0:
        raise RuntimeError('ffmpeg 切片段失败')
    return cut


def main():
    from services import cloud_asr

    t0 = time.time()
    clip = download_clip_sections(600)
    if clip is not None:
        print(f'[download-sections] OK: {clip.name} {clip.stat().st_size // 1024} KB, '
              f'{time.time() - t0:.1f}s（B1 姿势可用）')
    else:
        clip = asyncio.run(download_full_and_cut(600))
        print(f'[download] fallback 整段+切片 {time.time() - t0:.1f}s')

    t1 = time.time()
    _srt, txt, segs = cloud_asr.transcribe_audio_cloud(str(clip), provider='xingchen')
    elapsed = time.time() - t1
    print(f'[transcribe] xingchen: {elapsed:.1f}s | 条数={segs}')

    body = Path(txt).read_text(encoding='utf-8')
    plain = ''.join(re.sub(r'\[[^\]]*\]\s*', '', ln) for ln in body.splitlines())
    chars = len(re.sub(r'\s', '', plain))
    print(f'[quality] 字数={chars}')
    for term in ('内关', '太冲', '扎'):
        print(f'[quality] 术语 {term}: {plain.count(term)}')
    kana = re.findall(r'[\u3040-\u30ff]', plain)
    print(f'[quality] 假名幻觉: {len(kana)} 处 {kana[:8]}')

    dest = TASK_DIR / 'free-eval-output.txt'
    dest.write_text(body, encoding='utf-8')
    print(f'[saved] {dest}')
    print('[baseline] mimo: 147 条 / 2077 字 / 内关·太冲·扎 全对 / 假名 0 / 9.4s')


if __name__ == '__main__':
    main()
