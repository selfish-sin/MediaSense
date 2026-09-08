"""
YouTube Tool V2 - 配置文件
所有路径、模型、代理、默认值集中管理，不再硬编码
"""
import os
from pathlib import Path

# ============ 项目路径 ============
BASE_DIR = Path(__file__).parent
_TEST_DATA_DIR = os.getenv("YT_TEST_DATA_DIR")
DATA_DIR = Path(_TEST_DATA_DIR).expanduser() if _TEST_DATA_DIR else BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_DIR = DATA_DIR / "downloads"
DB_PATH = DATA_DIR / "youtube_tool.db"

# Storage cleanup defaults. Media pruning is never enabled by configuration alone.
CLEANUP_STAGED_TTL_HOURS = int(os.getenv('YT_CLEANUP_STAGED_TTL_HOURS', '72'))
CLEANUP_ORPHAN_TTL_DAYS = int(os.getenv('YT_CLEANUP_ORPHAN_TTL_DAYS', '14'))
CLEANUP_EXPORT_TTL_DAYS = int(os.getenv('YT_CLEANUP_EXPORT_TTL_DAYS', '7'))
CLEANUP_MEDIA_TTL_DAYS = int(os.getenv('YT_CLEANUP_MEDIA_TTL_DAYS', '7'))
CLEANUP_QUARANTINE_TTL_DAYS = int(os.getenv('YT_CLEANUP_QUARANTINE_TTL_DAYS', '30'))
CLEANUP_MAX_ITEMS = int(os.getenv('YT_CLEANUP_MAX_ITEMS', '1000'))
CLEANUP_MAX_BYTES = int(os.getenv('YT_CLEANUP_MAX_BYTES', str(20 * 1024**3)))

# 确保目录存在
DATA_DIR.mkdir(exist_ok=True)
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ============ Whisper 模型路径（与 config.py 一致：backend 上一级的 models 目录） ============
WHISPER_BASE = BASE_DIR.parent / "models"
LOCAL_MODEL_DIRS = {
    "tiny":   WHISPER_BASE / "whisper_tiny",
    "base":   WHISPER_BASE / "whisper_base",
    "small":  WHISPER_BASE / "whisper_small",
    "medium": WHISPER_BASE / "whisper_medium",
}
DEFAULT_WHISPER_MODEL = "base"

# ============ 服务端口 ============
SERVER_PORT = 7001
SERVER_HOST = "0.0.0.0"

# ============ 智谱 API Key（从环境变量读取，避免泄露到 Git） ============
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")

# ============ 代理配置 ============
PROXY = ""  # 需要时设置，如 "http://127.0.0.1:7890"

# 走系统代理不稳定的域名 → 启动时合并进 NO_PROXY，直连访问（httpx 全客户端生效）。
# 适用场景：自建 new-api 中转，Clash 代理隧道对该域名 ConnectError、直连正常。
# 例：PROXY_BYPASS_HOSTS = ["relay.example.com"]
PROXY_BYPASS_HOSTS = []  # 需要时填写域名列表

# ============ AI 模型（在 /models 页面管理，API Key 存数据库） ============
DEFAULT_MODEL = "deepseek-v4-flash"

# ============ 翻译语言列表 ============
TRANSLATE_LANGS = {
    "zh-CN": {"name": "简体中文", "code": "zh-CN"},
    "zh-TW": {"name": "繁体中文", "code": "zh-TW"},
    "en":    {"name": "英语", "code": "en"},
    "ja":    {"name": "日语", "code": "ja"},
    "ko":    {"name": "韩语", "code": "ko"},
    "fr":    {"name": "法语", "code": "fr"},
    "de":    {"name": "德语", "code": "de"},
    "es":    {"name": "西班牙语", "code": "es"},
    "ru":    {"name": "俄语", "code": "ru"},
}

# ============ 总结类型预设（15种，与功能手册完全对齐 + critique增强） ============
# key 必须与 prompts.py SUMMARY_PROMPTS 的 key 完全一致
# 名称/描述/适用场景/组织策略 均来自功能手册_完整版.md
SUMMARY_TYPES = {
    "summary":    {"name": "综合总结",   "desc": "通用深度总结",             "applicable": "通用",       "strategy": "argument_progression", "icon": "Document",        "category": "general"},
    "chapter":    {"name": "章节总结",   "desc": "逐章深度分析",             "applicable": "讲座/课程",  "strategy": "original_narrative",    "icon": "Notebook",      "category": "general"},
    "core":       {"name": "核心要点",   "desc": "快速抓论点",               "applicable": "快速浏览",   "strategy": "importance_descending",  "icon": "Aim",           "category": "general"},
    "notes":      {"name": "学习笔记",   "desc": "系统性学术笔记",           "applicable": "学习复习",   "strategy": "knowledge_hierarchy",   "icon": "NotebookPen",    "category": "general"},
    "mindmap":    {"name": "思维导图",   "desc": "可视化结构",               "applicable": "概念梳理",   "strategy": "concept_network",      "icon": "Share",          "category": "structured"},
    "qa":         {"name": "问答整理",   "desc": "提取问答对",               "applicable": "问答/访谈",  "strategy": "question_answer_pairing","icon": "ChatDotRound",  "category": "structured"},
    "timeline":   {"name": "时间线梳理", "desc": "历史事件/论证推进",         "applicable": "叙事/论证",  "strategy": "temporal_logic",        "icon": "Timer",          "category": "structured"},
    "debate":     {"name": "观点碰撞",   "desc": "多人争论/辩论",             "applicable": "辩论/讨论",  "strategy": "position_based",        "icon": "ScaleToOriginal","category": "structured"},
    "critique":   {"name": "批判分析",   "desc": "优缺点深度分析",            "applicable": "学术批判",   "strategy": "argument_progression",  "icon": "Warning",        "category": "deep"},
    "ramble":     {"name": "话题漫谈",   "desc": "闲聊/播客",                 "applicable": "播客/闲聊",  "strategy": "topic_flow",            "icon": "TrendCharts",   "category": "deep"},
    "tutorial":   {"name": "步骤提炼",   "desc": "教程/操作演示",             "applicable": "教程/演示",  "strategy": "step_sequence",         "icon": "Promotion",     "category": "deep"},
    "news":       {"name": "资讯整理",   "desc": "新闻/信息播报",             "applicable": "新闻/资讯",  "strategy": "five_w_one_h",          "icon": "Reading",       "category": "deep"},
    "quick":      {"name": "快速判断",   "desc": "30秒决定值不值得看",        "applicable": "快速筛选",  "strategy": "density_assessment",    "icon": "MagicStick",    "category": "quick"},
    "custom":     {"name": "自定义",     "desc": "完全自由编辑提示词",        "applicable": "任意",       "strategy": "",                      "icon": "EditPen",       "category": "general"},
    "translate":  {"name": "全文翻译",   "desc": "学术级翻译",               "applicable": "翻译需求",   "strategy": "original_paragraph",    "icon": "Switch",        "category": "translate"},
}

# 分类名映射（前端筛选用）
CATEGORY_NAMES = {
    "general":    "通用",
    "structured": "结构化",
    "deep":       "深度",
    "quick":      "快速",
    "translate":  "翻译",
}

# ============ 总结选项预设（与功能手册完全对齐） ============
SUMMARY_OPTIONS = {
    "word_count": {
        "name": "字数",
        "options": ["500", "3000", "5000", "10000", "20000", "自定义"],
        "default": "3000",
    },
    "tone": {
        "name": "语气",
        "options": ["学术严谨", "通俗科普", "口语化", "新闻客观", "正式商务", "自定义"],
        "default": "学术严谨",
    },
    "format": {
        "name": "格式",
        "options": ["Markdown", "纯文本", "结构化大纲", "要点列表", "自定义"],
        "default": "Markdown",
    },
    "purpose": {
        "name": "目的",
        "options": ["学习笔记", "素材收集", "快速浏览", "深度分析", "内容归纳", "自定义"],
        "default": "学习笔记",
    },
}

# ============ 场景预设（与功能手册6个按钮完全对齐） ============
SCENARIOS = {
    "video_edit":    {"name": "🎬 视频剪辑·做字幕", "word_count": "不限",    "tone": "口语化",  "format": "要点列表", "purpose": "素材收集", "summary_type": "core"},
    "reading":       {"name": "📄 全文精读翻译",    "word_count": "不限",    "tone": "学术严谨", "format": "纯文本",   "purpose": "内容归纳", "summary_type": "translate"},
    "study":         {"name": "📝 学习做笔记",      "word_count": "3000",    "tone": "学术严谨", "format": "Markdown", "purpose": "学习笔记", "summary_type": "notes"},
    "skim":          {"name": "👀 快速扫一眼",      "word_count": "1000",    "tone": "通俗科普", "format": "要点列表", "purpose": "快速浏览", "summary_type": "summary"},
    "deep_research": {"name": "🔬 深度研究分析",    "word_count": "不限",    "tone": "学术严谨", "format": "结构化大纲","purpose": "深度分析", "summary_type": "deep_research"},
    "textbook":      {"name": "📖 教科书模式",      "word_count": "20000",   "tone": "学术严谨", "format": "结构化大纲","purpose": "教材编写", "summary_type": "textbook"},
    "podcast":       {"name": "🎤 播客/采访整理",    "word_count": "5000",    "tone": "口语化",  "format": "要点列表", "purpose": "素材收集", "summary_type": "qa"},
}

# ============ B站 cookies 文件 ============
BILIBILI_COOKIE_FILE = DATA_DIR / "bilibili_cookies.json"

# ============ 快捷标签（深度模式研究指令，与功能手册完全对齐） ============
QUICK_TAGS = {
    "argument_tracking":   {"name": "论证追踪",     "desc": "完整追踪每一步论证推进，不跳过"},
    "concept_analysis":    {"name": "概念辨析",     "desc": "精确辨析关键概念，不降维"},
    "examples_complete":   {"name": "例证不遗漏",   "desc": "所有案例/数据/故事逐条记录"},
    "timeline_tag":        {"name": "时间线",       "desc": "按时间/逻辑顺序梳理重要节点"},
    "data_verification":   {"name": "数据与实验核查","desc": "逐条核对数据、方法、误差范围"},
    "methodology_trace":   {"name": "方法论追踪",   "desc": "梳理研究方法选择及其局限"},
    "comparison_analysis": {"name": "对比分析",     "desc": "逐项列出比较维度和结论"},
    "practical_implications":{"name": "实践意涵",   "desc": "对实践/政策/工程的参考价值"},
    "theory_dialogue":     {"name": "理论对话",     "desc": "标注与哪些理论家形成对话"},
    "academic_history":    {"name": "学术史定位",   "desc": "在学科谱系中的位置"},
    "historical_context":  {"name": "历史背景",     "desc": "揭示产生的历史条件"},
    "thought_lineage":     {"name": "思想史谱系",   "desc": "与思想史传统的继承/批评/修正关系"},
    "critical_analysis":   {"name": "批判性审查",   "desc": "审视分析框架的意识形态预设"},
    "marxist_framework":   {"name": "马主义框架",   "desc": "用马主义框架分析（标注仅供参考）"},
}
