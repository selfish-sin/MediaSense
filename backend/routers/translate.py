"""
YouTube Tool V2 - 翻译路由

★ 双语对照端点 /translate/bilingual 已删除（2026-09-12）：
  前端双语块在 09-06 死代码清理中移除后该端点无任何调用方；
  翻译能力统一走任务流程 Step 4/5（main.py + ai_processor.translate_subtitles）。
"""
import logging
from fastapi import APIRouter

from config import TRANSLATE_LANGS

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/langs")
async def get_translate_langs():
    """获取翻译语言列表（工作台「翻译」子选项的目标语言下拉数据源）"""
    langs = {}
    for code, cfg in TRANSLATE_LANGS.items():
        langs[code] = {"name": cfg["name"], "code": cfg["code"]}
    return {"langs": langs}
