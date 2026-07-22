"""
YouTube Tool V2 - 翻译路由
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from config import TRANSLATE_LANGS, DEFAULT_MODEL
from db.model_repo import get_model_by_key
from db.job_repo import get_job, update_job

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/langs")
async def get_translate_langs():
    """获取翻译语言列表"""
    langs = {}
    for code, cfg in TRANSLATE_LANGS.items():
        langs[code] = {"name": cfg["name"], "code": cfg["code"]}
    return {"langs": langs}


class BilingualRequest(BaseModel):
    text: str                       # 原文文本
    target_lang: str = "zh"         # 目标语言代码
    model: Optional[str] = None     # AI 模型 key
    job_id: Optional[str] = None    # 关联任务ID（可选，用于保存翻译结果）


@router.post("/bilingual")
async def bilingual_translate(req: BilingualRequest):
    """
    双语对照翻译
    - 接收原文 + 目标语言，调用 AI 返回译文
    - 如果传了 job_id，同时把译文保存到任务 files.translation_text
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="原文不能为空")

    # 获取目标语言名称
    lang_cfg = TRANSLATE_LANGS.get(req.target_lang)
    lang_name = lang_cfg["name"] if lang_cfg else req.target_lang

    # 获取模型
    model_key = req.model or DEFAULT_MODEL
    model_cfg = get_model_by_key(model_key) or get_model_by_key(DEFAULT_MODEL)
    if not model_cfg:
        raise HTTPException(status_code=400, detail=f"模型 {model_key} 不存在或未配置API Key")

    # 构建翻译 prompt（学术级，保持段落结构）
    prompt = f"""请将以下文本翻译为{lang_name}。
要求：
1. 保持原文的段落结构和标题层级（Markdown格式）
2. 翻译准确流畅，学术性内容保持学术语体
3. 只输出译文，不要添加解释或说明

原文：
{req.text}"""

    try:
        from services.ai_processor import _call_ai
        translation, in_tok, out_tok = await _call_ai(
            model_cfg, prompt, timeout=120.0
        )
    except Exception as e:
        logger.error(f"翻译失败: {e}")
        raise HTTPException(status_code=500, detail=f"翻译失败: {e}")

    # 若传了 job_id，将译文保存进任务
    if req.job_id:
        job = get_job(req.job_id)
        if job:
            # ★ files 经 job_repo._row_to_dict 已反序列化为 dict，直接用；
            #   仅历史脏数据（仍是 str）才需要 json.loads
            files = job.get('files') or {}
            if isinstance(files, str):
                import json
                try:
                    files = json.loads(files)
                except (json.JSONDecodeError, TypeError):
                    files = {}
            if not isinstance(files, dict):
                files = {}
            files['translation_text'] = translation
            files['translation_lang'] = req.target_lang
            # ★ update_job 内部会对 files 做 json.dumps，这里必须传 dict；
            #   若传 json.dumps 后的 str 会被二次编码，读回变 str 导致
            #   JobResponse.files(Dict) 校验失败 → GET /api/jobs 整体 500
            update_job(req.job_id, files=files)

    return {
        "success": True,
        "translation": translation,
        "target_lang": req.target_lang,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }
