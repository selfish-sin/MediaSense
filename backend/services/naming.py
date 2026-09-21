"""文件命名单点（导出 / 下载素材公共）。

**为什么单独立一个模块**：导出 4 格式、批量 ZIP、下载素材三处此前各自处理文件名，
`routers/export.py._sanitize_title` 会把《》、，、-、· 等标点**全部删掉**并硬截断到
50 字符，长标题因此变成一长串粘连文字（实测：「北京中医药大学刘景源讲温病学72集全
国家中医药管理局02年发起全国示范教程_p34_34温病学春温热灼真阴.docx」），用户根本
认不出是哪个视频；标题为空时还会落到 `AI总结_<job_id>` 这种零信息量名字。
本模块是唯一实现，**勿在别处再抄一份文件名清洗逻辑**。

不变量：
- 只剔除 Windows 非法字符（``\\ / : * ? " < > |``）与控制字符，**保留中文标点**与空格；
- 结果长度 ≤ ``max_len`` 个码点，且不以点号/空格结尾（Windows 路径不允许）；
- 空结果不编造名字：由调用方经 ``fallback`` / ``export_basename`` 兜底。
"""
import re

# Windows 保留字符 + ASCII 控制符 + DEL
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r'\s+')
# B站 BV 号（固定 BV + 10 位）
_BV_RE = re.compile(r'BV[0-9A-Za-z]{10}')
# YouTube：watch?v= / youtu.be/ / shorts/ / embed/ / live/
_YT_RE = re.compile(r'(?:v=|youtu\.be/|shorts/|embed/|live/)([0-9A-Za-z_-]{11})')

DEFAULT_MAX_LEN = 80


def safe_basename(name, *, max_len: int = DEFAULT_MAX_LEN, fallback: str = '') -> str:
    """清洗为可读的文件名主干（不含扩展名）。

    保留中文标点与空格（只折叠连续空白为单个空格），超长按码点截断。

    ★ 顺序敏感：必须**先**把 \\t\\n\\r 等空白折叠成空格**再**剔控制字符——反过来
    会把换行/制表符直接删掉，导致相邻文字粘连（「多个\\t空白」→「多个空白」）。
    """
    text = _WHITESPACE.sub(' ', str(name or ''))
    text = _ILLEGAL_CHARS.sub('', text).strip(' .')
    if len(text) > max_len:
        text = text[:max_len].strip(' .')
    return text or fallback


def extract_video_id(url) -> str:
    """从 URL 提取平台视频 ID（B站 BV 号优先，其次 YouTube video id）。"""
    if not url:
        return ''
    url = str(url)
    match = _BV_RE.search(url)
    if match:
        return match.group(0)
    match = _YT_RE.search(url)
    if match:
        return match.group(1)
    return ''


def export_basename(job, *, max_len: int = DEFAULT_MAX_LEN) -> str:
    """任务导出/下载名主干：标题 → 平台视频 ID（BV/YT）→ ``job_{id}``。

    兜底链保证导出名始终可辨识，杜绝 ``AI总结_<job_id>`` 这类无信息量名字。
    """
    job = job or {}
    base = safe_basename(job.get('title'), max_len=max_len)
    if base:
        return base
    video_id = extract_video_id(job.get('input_url'))
    if video_id:
        return video_id
    job_id = job.get('id') or ''
    if not job_id:
        return 'result'
    return safe_basename(f"job_{job_id}", max_len=max_len, fallback='result')
