"""
YouTube Tool V2 - 任务 AI 域路由（重新总结 / 总结历史 / 标签 / 思维导图）

从 routers/jobs.py 按域拆出，全部挂 /api/jobs 前缀，对外路径零变化。
"""
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from models.schemas import SuccessResponse
from db.job_repo import (get_job, update_job, save_summary_version, get_summary_history,
                         get_summary_version, append_summary_version)
from db.model_repo import get_default_model_key
from config import DEFAULT_MODEL
from services.naming import derived_sibling

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{job_id}/summarize")
async def resummarize_job(job_id: str, body: dict):
    """用不同参数重新总结（基于已有转录，跳过下载/转录步骤）"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")

    # 检查job状态：completed 或 failed（AI 阶段失败且保留转录文本）可重新总结，
    # 仅重跑 AI 步骤，不重新下载/转录
    if job['status'] not in ('completed', 'failed'):
        raise HTTPException(status_code=400, detail="只能对已完成或失败（且保留转录文本）的任务重新总结")

    # 获取已有转录文本
    files = job.get('files') or {}
    txt_path = files.get('txt', '')
    if not txt_path or not Path(txt_path).exists():
        raise HTTPException(status_code=400, detail="任务没有可用的转录文本")

    # 读取转录文本（线程池避免阻塞事件循环）
    text_content = await asyncio.to_thread(Path(txt_path).read_text, encoding='utf-8')

    if not text_content.strip():
        raise HTTPException(status_code=400, detail="转录文本为空")

    # 获取新参数
    summary_type = body.get('summary_type', job.get('summary_type', 'summary'))
    model = body.get('model') or job.get('model') or get_default_model_key()
    # Long-text auto-routing in resummarize: >=5000 chars -> deepseek
    if model == DEFAULT_MODEL and len(text_content) >= 5000:
        from db.model_repo import get_model_by_key
        if get_model_by_key('deepseek-v4-flash'):
            model = 'deepseek-v4-flash'
            logger.info(f'Resummarize: long text ({len(text_content)} chars), auto-switched model: {model}')
    # ★ 键存在但值为 null 时 dict.get 会返回 None（默认值只在键缺失时生效），
    #   直接 `opts.get(...)` 会抛 AttributeError → 500。加 `or` 兜底。
    opts = body.get('options') or job.get('options') or {}
    # ★ 模块化提示词：优先使用请求体中的 dimensions，其次原始 job 的 options.dimensions
    dimensions = body.get('dimensions') or opts.get('dimensions')

    summary_options = {
        'word_count': opts.get('word_count', '不限'),
        'tone': opts.get('tone', '中性'),
        'format': opts.get('format', 'markdown'),
        'purpose': opts.get('purpose', '学习'),
        'edited_prompt': opts.get('edited_prompt', ''),
        'custom_prompt': opts.get('custom_prompt', ''),
        'full_override': opts.get('full_override', ''),
        'org_strategy': opts.get('org_strategy', 'argument_progression'),
    }

    # ★ 保存当前版本到 summary_history（覆盖前先存档）
    #   2026-10-13 修复：指针可能与历史表脱节（重新总结失败/取消只改状态不写历史），
    #   直接拿 current_version 做 INSERT OR REPLACE 会覆盖掉别的已归档版本。
    #   指针命中已有行 → 幂等刷新同一版本（内容本就是同一份）；
    #   指针是孤儿（历史非空但没有这一版）→ 改用追加，绝不覆盖。
    current_version = job.get('summary_version', 1)
    if job.get('result_text'):
        _history = get_summary_history(job_id)
        _hit = any(h.get('version') == current_version for h in _history)
        if _history and not _hit:
            _archived = append_summary_version(
                job_id=job_id,
                summary_type=job.get('summary_type', 'summary'),
                model=job.get('model', ''),
                result_text=job['result_text'],
                tags=job.get('tags', []),
                token_usage=job.get('token_usage', {}),
                files=job.get('files', {}),
            )
            logger.info(f"任务 {job_id} 版本指针 v{current_version} 与历史脱节，已改为追加存档为 v{_archived}")
        else:
            saved = save_summary_version(
                job_id=job_id, version=current_version,
                summary_type=job.get('summary_type', 'summary'),
                model=job.get('model', ''),
                result_text=job['result_text'],
                tags=job.get('tags', []),
                token_usage=job.get('token_usage', {}),
                files=job.get('files', {}),
            )
            if saved:
                logger.info(f"任务 {job_id} v{current_version} 已存档到 summary_history")

    # 更新job状态
    update_job(job_id, status='summarizing', progress_msg='重新AI处理中...',
               summary_type=summary_type, model=model)

    # 运行AI处理
    try:
        from services.ai_processor import ai_process, generate_tags
        from main import sio

        result_text, in_tok, out_tok = await ai_process(
            text_content, summary_type=summary_type, model=model,
            sio=sio, job_id=job_id, summary_options=summary_options,
            dimensions=dimensions,
            bypass_cache=True,  # ★ 显式重跑（"重新总结"）不读缓存：用户要的是重新生成
        )

        # ★ 契约检查（镜像 main.py）：AI 层失败返回 [处理失败: ...] 哨兵串时必须显式失败，
        #   否则会被当作正常结果写盘并标记 completed
        if isinstance(result_text, str) and result_text.lstrip().startswith('[处理失败'):
            raise Exception(result_text.strip().strip('[]'))
        # ★ 空结果防线（2026-10-06）：零正文（如思考型模型只吐思考过程）不允许标 completed
        if not str(result_text or '').strip():
            raise Exception('AI 未返回任何内容（模型可能只输出了思考过程），请换模型重试')

        # 保存新结果文件（派生文件名走 naming 单点，勿用 str.replace('.txt', ...)）
        result_md_path = derived_sibling(
            txt_path, stem_suffix=f'__{summary_type}', extension='.md',
        )
        with open(result_md_path, 'w', encoding='utf-8') as f:
            f.write(result_text)

        # 自动打标签
        #   ★ 契约（2026-10-13）：generate_tags 返回 None = 调用失败（兜底环全挂），
        #     返回 [] = 调用成功但没解析出标签。失败时**保留任务原有标签**——
        #     此前一律当 [] 写库，用户点一次「生成标签」就把已有标签清空了。
        tags = None
        try:
            tags = await generate_tags(text_content, model=model, sio=sio, job_id=job_id,
                                       bypass_cache=True)
        except Exception as e:
            logger.warning(f"任务 {job_id} 自动打标签异常: {e}")
            tags = None
        if tags is None:
            tags = list(job.get('tags') or [])
            logger.warning(f"任务 {job_id} 自动打标签失败，已保留原有标签（{len(tags)} 个）")

        # 更新job
        new_files = dict(files)
        new_files['result_md'] = result_md_path
        # ★ 必须检查写入结果（2026-10-13 修复 P1-8）：`update_job` 对「已取消」任务有
        #   终态保护——写入被挡下时返回 False 且 result_text 保持旧值。此前忽略返回值
        #   继续往下走，于是 append_summary_version 照样存了新版本、summary_version
        #   指针也照样前移，而 jobs.result_text 还是**上一版正文**。因为
        #   `GET summary-history/{version}` 在 version == summary_version 时直接返回
        #   jobs.result_text，用户点最新的那一版，看到的却是旧内容。
        _written = update_job(job_id, status='completed',
                              result_text=result_text,
                              summary_type=summary_type,
                              model=model,
                              files=new_files,
                              token_usage={'input': in_tok, 'output': out_tok},
                              tags=tags,
                              error=None)
        if not _written:
            logger.warning(
                f"任务 {job_id} 重新总结写入被拒（任务已被取消），"
                f"不存档新版本、不前移 summary_version 指针（已生成的 md 未入库: {result_md_path.name}）"
            )
            raise HTTPException(status_code=409,
                                detail="任务已被取消，重新总结结果未写入；如仍需该结果请重新发起")

        # ★ 保存新版本到 summary_history：版本号在事务内推导（append_summary_version），
        #   不再由调用方 `current_version + 1` 计算——并发「重新总结」会算出同一个
        #   版本号互相覆盖，且 current_version 可能与历史表脱节。
        new_version = append_summary_version(
            job_id=job_id,
            summary_type=summary_type, model=model,
            result_text=result_text,
            tags=tags,
            token_usage={'input': in_tok, 'output': out_tok},
            files=new_files,
        )
        if new_version is None:
            logger.error(f"任务 {job_id} 新总结版本存档失败，summary_version 指针保持原值")
        else:
            update_job(job_id, summary_version=new_version)

        logger.info(f"任务 {job_id} 重新总结完成 v{new_version}: type={summary_type}, tokens in={in_tok} out={out_tok}")
        return {
            "success": True,
            "job_id": job_id,
            "result_text": result_text,
            "token_usage": {"input": in_tok, "output": out_tok},
            "tags": tags,
            "message": "重新总结完成"
        }

    except HTTPException:
        # ★ 透传：try 块内部显式抛出的 4xx（如「任务已被取消，拒绝写入」）是有意契约，
        #   不能被下面的兜底分支改写成 500，也不能连带把任务标成 failed。
        raise
    except Exception as e:
        from services.ai_processor import TaskCancelledError
        if isinstance(e, TaskCancelledError):
            logger.info(f"任务 {job_id} 重新总结被用户取消")
            update_job(job_id, status='cancelled', error='用户取消')
            raise HTTPException(status_code=499, detail="任务已被用户取消")
        logger.error(f"任务 {job_id} 重新总结失败: {e}")
        update_job(job_id, status='failed', error=str(e))
        raise HTTPException(status_code=500, detail=f"重新总结失败: {str(e)}")


# ============ 总结历史版本 API ============

@router.get("/{job_id}/summary-history")
async def get_job_summary_history(job_id: str):
    """获取任务的所有总结历史版本列表"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    history = get_summary_history(job_id)
    # 如果 history 为空但 latest 已有 result_text，把当前结果作为 v1 返回
    if not history and job.get('result_text'):
        history = [{
            'version': job.get('summary_version', 1),
            'summary_type': job.get('summary_type', 'summary'),
            'model': job.get('model', ''),
            'result_text': job['result_text'],
            'tags': job.get('tags', []),
            'token_usage': job.get('token_usage', {}),
            'files': job.get('files', {}),
            'created_at': job.get('created_at', ''),
        }]
    # 精简返回：排除 result_text 全文（太大了），前端点击版本时再加载全文
    summary_list = []
    for h in history:
        text_preview = (h.get('result_text', '') or '')[:100]
        summary_list.append({
            'version': h.get('version', 0),
            'summary_type': h.get('summary_type', ''),
            'model': h.get('model', ''),
            'created_at': h.get('created_at', ''),
            'text_preview': text_preview,
            'char_count': len(h.get('result_text', '') or ''),
        })
    return {
        "success": True,
        "job_id": job_id,
        "current_version": job.get('summary_version', 1),
        "total_versions": len(summary_list),
        "versions": summary_list,
    }


@router.get("/{job_id}/summary-history/{version}")
async def get_job_summary_version(job_id: str, version: int):
    """获取任务指定版本的总结全文"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    # 如果请求的是当前最新版本，直接从 jobs 表返回
    if version == job.get('summary_version', 1) and job.get('result_text'):
        return {
            "success": True,
            "job_id": job_id,
            "version": version,
            "summary_type": job.get('summary_type', ''),
            "model": job.get('model', ''),
            "result_text": job['result_text'],
            "tags": job.get('tags', []),
            "created_at": job.get('created_at', ''),
        }
    # 否则从 summary_history 表查询
    ver = get_summary_version(job_id, version)
    if not ver:
        raise HTTPException(status_code=404, detail=f"版本 {version} 不存在")
    return {
        "success": True,
        "job_id": job_id,
        "version": ver.get('version', version),
        "summary_type": ver.get('summary_type', ''),
        "model": ver.get('model', ''),
        "result_text": ver.get('result_text', ''),
        "tags": ver.get('tags', []),
        "created_at": ver.get('created_at', ''),
    }


# ============ 标签管理 API ============

@router.post("/{job_id}/generate-tags")
async def generate_job_tags(job_id: str, body: dict = None):
    """对已完成的任务用 AI 生成分类标签"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if job['status'] != 'completed':
        raise HTTPException(status_code=400, detail="只能对已完成的任务生成标签")

    files = job.get('files') or {}
    txt_path = files.get('txt', '')
    if not txt_path or not Path(txt_path).exists():
        raise HTTPException(status_code=400, detail="任务没有可用的转录文本")

    text_content = await asyncio.to_thread(Path(txt_path).read_text, encoding='utf-8')

    if not text_content.strip():
        raise HTTPException(status_code=400, detail="转录文本为空")

    model = (body or {}).get('model', job.get('model', DEFAULT_MODEL))

    from services.ai_processor import generate_tags
    from main import sio
    try:
        tags = await generate_tags(text_content, model=model, sio=sio, job_id=job_id,
                                   bypass_cache=True)  # ★ 显式重跑不读缓存
    except Exception as e:
        logger.error(f"AI 生成标签失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI 生成标签失败: {str(e)}")

    # ★ 2026-10-13：generate_tags 返回 None = 兜底环内全部候选调用失败。
    #   此前它吞异常返回 []，这里就会把用户已有的标签清空并回「已生成 0 个标签」——
    #   失败伪装成成功且不可恢复。现在失败不写库，并返回可行动文案。
    if tags is None:
        raise HTTPException(
            status_code=502,
            detail="AI 生成标签失败：模型全部候选调用均失败，已保留原有标签未改动。请到「模型管理」检查模型可用性后重试。",
        )

    update_job(job_id, tags=tags)
    logger.info(f"任务 {job_id} 标签已生成: {tags}")
    return SuccessResponse(message=f"已生成 {len(tags)} 个标签", data={"tags": tags})


# ============ 思维导图 API ============

@router.post("/{job_id}/mindmap")
async def generate_job_mindmap(job_id: str, body: dict = None):
    """对已完成的任务生成 markmap 思维导图

    ★ 2026-10-11：body 可带 version —— 历史详情页正在查看 vN 时，思维导图必须基于 vN，
      否则页面停在 v1、导图却是 v2 的结构（与导出同一个毛病）。
      不传 version = 基于当前最新版，行为与原来完全一致。
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if not job.get('result_text'):
        raise HTTPException(status_code=400, detail="任务没有总结结果，无法生成思维导图")

    body = body or {}
    version = body.get('version')
    result_text = job['result_text']
    if version and version != (job.get('summary_version') or 1):
        ver = get_summary_version(job_id, version)
        if not ver or not (ver.get('result_text') or '').strip():
            raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在或没有结果，无法生成思维导图")
        result_text = ver['result_text']

    model = body.get('model', job.get('model', DEFAULT_MODEL))
    from services.ai_processor import generate_mindmap
    from main import sio
    try:
        mindmap_md = await generate_mindmap(
            result_text=result_text,
            title=job.get('title', ''),
            model=model,
            sio=sio,
            job_id=job_id,
            bypass_cache=True,  # ★ 显式重跑（"重新生成"）不读缓存
        )
    except Exception as e:
        logger.error(f"思维导图生成失败: {e}")
        raise HTTPException(status_code=500, detail=f"思维导图生成失败: {str(e)}")

    # ★ 空结果显式失败（2026-10-06）：generate_mindmap 失败时返回 ''，
    #   若按 success 返回空 md，前端只能给出含糊的"返回内容为空"
    if not (mindmap_md or '').strip():
        raise HTTPException(status_code=500,
                            detail="思维导图生成为空：所有候选模型均未返回内容，请查看日志或换模型重试")

    return SuccessResponse(message="思维导图已生成", data={"mindmap_md": mindmap_md})


@router.put("/{job_id}/tags")
async def update_job_tags(job_id: str, body: dict):
    """手动更新任务的标签列表"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")

    tags = body.get('tags', [])
    if not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="tags 必须是字符串数组")

    # 去重 + 去空格
    tags = list(set(t.strip() for t in tags if t and t.strip()))
    update_job(job_id, tags=tags)
    logger.info(f"任务 {job_id} 标签已更新: {tags}")
    return SuccessResponse(message=f"标签已更新 ({len(tags)} 个)", data={"tags": tags})
