'''真实内容质量评测（中医讲座等样本）：转录引擎横评 + 摘要模型横评。

⚠️ 会产生真实外部调用（云端 ASR/LLM 花钱、本地引擎耗时）——样本与引擎/模型范围
  由运行者决定；付费档 cloud 只在显式列入 --engines 时才会被调用。

样本清单格式（JSON，单对象或数组），字段均可选除注明外：
{
  "name": "中药学-p41",                    // 必填，样本名
  "audio": "path/to/audio.m4a",           // 转录评测必填；只评摘要可省
  "transcript": "path/to/ref.txt",        // 人工参考文本（算 CER 用；也是摘要评测输入）
  "terms": ["黄芪", "足三里"],             // 术语命中清单
  "dosages": ["黄芪30g"],                  // 剂量命中清单
  "language": "zh",                       // 转录语言，缺省 zh
  "summary_dimensions": {"role": "tcm"}   // 摘要提示词维度覆盖，可省
}

跑法示例（在 backend/ 目录）：
  ./venv/Scripts/python.exe scripts/eval_quality.py \
      --samples ../eval/samples/tcm.json \
      --engines firered,cloud_free \
      --models glm-5-2 \
      --out ../eval/reports/run1

报告与原始结果 JSON 写入 --out 目录；不自动删除、不进 Git（.gitignore eval/reports/）。
'''
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
for _p in (str(BACKEND_DIR), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eval_metrics as em  # noqa: E402 — CLI 入口先注入 sys.path

# 转录 txt 的行前缀（[MM:SS - MM:SS] / [说话人X]），评测前剥掉，与纯净阅读版同口径
_TS_PREFIX_RE = re.compile(r'^\s*\[\d{1,2}:\d{2}(?::\d{2})?\s*(?:-\s*\d{1,2}:\d{2}(?::\d{2})?)?\]\s*')
_SPEAKER_RE = re.compile(r'\s*\[说话人\d+\]')


def load_samples(path: Path) -> list:
    data = json.loads(path.read_text(encoding='utf-8'))
    samples = data if isinstance(data, list) else [data]
    for s in samples:
        if not isinstance(s, dict) or not s.get('name'):
            raise ValueError(f'样本缺少 name 字段: {path}')
    return samples


def _read_text(path_str: str) -> str:
    if not path_str:
        return ''
    p = Path(path_str)
    return p.read_text(encoding='utf-8', errors='replace') if p.exists() else ''


def clean_transcript_text(raw: str) -> str:
    """剥时间戳/说话人标记，只留正文（指标口径与 txt_clean 一致）。"""
    lines = []
    for line in (raw or '').splitlines():
        line = _TS_PREFIX_RE.sub('', line)
        line = _SPEAKER_RE.sub('', line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


async def eval_transcription(sample: dict, engines: list, model_size: str,
                             use_cache: bool) -> list:
    """逐引擎真实转录并打分；任何引擎失败都记录原因，不中断其他引擎。"""
    from services.transcriber import transcribe_audio

    audio = sample.get('audio')
    if not audio or not Path(audio).exists():
        return [{'engine': e, 'ok': False, 'error': f'音频不存在: {audio}'} for e in engines]
    reference = _read_text(sample.get('transcript'))
    language = sample.get('language') or 'zh'

    rows = []
    for engine in engines:
        info: dict = {}
        t0 = time.time()
        try:
            _srt, txt_path, seg_count = await transcribe_audio(
                audio, model_size=model_size, language=language,
                engine=engine, bypass_cache=not use_cache, info=info,
            )
            text = clean_transcript_text(Path(txt_path).read_text(encoding='utf-8', errors='replace'))
            rows.append({
                'engine': engine, 'ok': True,
                'elapsed_s': round(time.time() - t0, 1),
                'seg_count': seg_count, 'txt_path': txt_path,
                'engine_actual': info.get('engine'), 'engine_label': info.get('engine_label'),
                'degraded': info.get('degraded'), 'cache': info.get('cache'),
                'metrics': em.metrics_dict(reference, text, sample.get('terms', []),
                                           sample.get('dosages', [])),
            })
        except Exception as e:
            rows.append({
                'engine': engine, 'ok': False,
                'elapsed_s': round(time.time() - t0, 1),
                'error': f'{type(e).__name__}: {e}',
            })
    return rows


async def eval_summary(sample: dict, transcript_text: str, models: list) -> list:
    """钉死单模型逐个生成摘要（不走兜底环），对比关键信息保留与无出处数字。"""
    from services.ai_processor import _call_ai, is_call_failed
    from db.model_repo import get_model_by_key
    from services.prompt_builder import build_modular_prompt, get_default_dimensions

    if not transcript_text.strip():
        return [{'model': m, 'ok': False, 'error': '无可用转录文本（缺参考文本且转录全部失败）'}
                for m in models]

    dims = dict(get_default_dimensions())
    dims.update(sample.get('summary_dimensions') or {})
    prompt = build_modular_prompt(dims, transcript_text)
    terms = sample.get('terms', [])
    dosages = sample.get('dosages', [])

    rows = []
    for key in models:
        cfg = get_model_by_key(key)
        if not cfg:
            rows.append({'model': key, 'ok': False, 'error': '模型不存在（DB 无此 key）'})
            continue
        t0 = time.time()
        try:
            content, in_tok, out_tok = await _call_ai(cfg, prompt, timeout=600.0, bypass_cache=True)
            ok = not is_call_failed(content)
            t_hit, t_total = em.term_coverage(content, terms)
            d_hit, d_total = em.term_coverage(content, dosages)
            rows.append({
                'model': key, 'ok': True, 'summary_ok': ok,
                'elapsed_s': round(time.time() - t0, 1),
                'tokens_in': in_tok, 'tokens_out': out_tok,
                'summary_len': len(content),
                'summary_text': content if ok else '',
                'metrics': {
                    'terms_hit': f'{t_hit}/{t_total}',
                    'terms_miss': [t for t, h in em.term_hits(content, terms) if not h],
                    'dosages_hit': f'{d_hit}/{d_total}',
                    'unsourced_numbers': em.missing_numbers(content, transcript_text),
                },
            })
        except Exception as e:
            rows.append({
                'model': key, 'ok': False,
                'elapsed_s': round(time.time() - t0, 1),
                'error': f'{type(e).__name__}: {e}',
            })
    return rows


def render_report(samples: list, results: dict) -> str:
    lines = ['# 真实内容质量评测报告', '',
             f'- 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
             f'- 样本数：{len(samples)}', '',
             '> CER 为字级相似度近似（归一化后 1-ratio），越低越好；',
             '> 术语/剂量为归一化子串命中；「无出处数字」是摘要幻觉代理指标。', '']
    for sample in samples:
        name = sample['name']
        lines.append(f'## 样本：{name}')
        r = results.get(name, {})
        trows = r.get('transcription') or []
        if trows:
            lines += ['', '### 转录引擎横评', '',
                      '| 引擎 | 实际引擎 | 降级 | 耗时s | CER | 术语 | 剂量 | 备注 |',
                      '|---|---|---|---|---|---|---|---|']
            for row in trows:
                if not row['ok']:
                    lines.append(f"| {row['engine']} | - | - | {row['elapsed_s']} | - | - | - | ❌ {row['error']} |")
                    continue
                m = row['metrics']
                note = f"缺术语: {'、'.join(m['terms_miss'][:5])}" if m['terms_miss'] else ''
                lines.append(
                    f"| {row['engine']} | {row.get('engine_label') or row.get('engine_actual') or '-'} "
                    f"| {'是' if row.get('degraded') else '否'} | {row['elapsed_s']} "
                    f"| {m['cer'] if m['cer'] is not None else '无参考'} "
                    f"| {m['terms_hit']} | {m['dosages_hit']} | {note} |")
        else:
            lines += ['', '（未运行转录评测）']
        srows = r.get('summary') or []
        if srows:
            lines += ['', '### 摘要模型横评', '',
                      '| 模型 | 耗时s | tokens(入/出) | 字数 | 术语保留 | 剂量保留 | 无出处数字 | 备注 |',
                      '|---|---|---|---|---|---|---|---|']
            for row in srows:
                if not row['ok']:
                    lines.append(f"| {row['model']} | {row['elapsed_s']} | - | - | - | - | - | ❌ {row['error']} |")
                    continue
                m = row['metrics']
                unsourced = '、'.join(m['unsourced_numbers'][:8]) or '无'
                note = '' if row.get('summary_ok') else '⚠ 内容异常（哨兵串）'
                lines.append(
                    f"| {row['model']} | {row['elapsed_s']} | {row['tokens_in']}/{row['tokens_out']} "
                    f"| {row['summary_len']} | {m['terms_hit']} | {m['dosages_hit']} | {unsourced} | {note} |")
        lines.append('')
    return '\n'.join(lines)


async def run(args: argparse.Namespace) -> int:
    samples = load_samples(Path(args.samples))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    engines = [e.strip() for e in args.engines.split(',') if e.strip()] if args.engines else []
    models = [m.strip() for m in args.models.split(',') if m.strip()] if args.models else []
    results: dict = {}

    for sample in samples:
        name = sample['name']
        print(f'══ 样本 {name} ══')
        entry: dict = {}
        t_rows = []
        if engines:
            print(f'  转录引擎：{", ".join(engines)}')
            t_rows = await eval_transcription(sample, engines, args.model_size, args.use_cache)
            for row in t_rows:
                mark = 'ok' if row['ok'] else 'FAIL'
                extra = f" CER={row['metrics']['cer']}" if row['ok'] and row['metrics']['cer'] is not None else ''
                print(f'    [{mark}] {row["engine"]} {row["elapsed_s"]}s{extra}'
                      if row['ok'] else f'    [{mark}] {row["engine"]} {row["error"]}')
        entry['transcription'] = t_rows

        if models:
            # 摘要评测输入：优先参考文本，否则取第一个成功引擎的转录
            ref_text = clean_transcript_text(_read_text(sample.get('transcript')))
            if not ref_text:
                for row in t_rows:
                    if row.get('ok') and row.get('txt_path'):
                        ref_text = clean_transcript_text(
                            Path(row['txt_path']).read_text(encoding='utf-8', errors='replace'))
                        break
            print(f'  摘要模型：{", ".join(models)}（输入 {len(ref_text)} 字）')
            entry['summary'] = await eval_summary(sample, ref_text, models)
            for row in entry['summary']:
                mark = 'ok' if row['ok'] else 'FAIL'
                print(f'    [{mark}] {row["model"]} {row["elapsed_s"]}s'
                      if row['ok'] else f'    [{mark}] {row["model"]} {row["error"]}')
        else:
            entry['summary'] = []

        results[name] = entry

    (out_dir / 'results.json').write_text(
        json.dumps({'samples': samples, 'results': results}, ensure_ascii=False, indent=2),
        encoding='utf-8')
    report = render_report(samples, results)
    (out_dir / 'report.md').write_text(report, encoding='utf-8')
    print(f'[ok] 报告: {out_dir / "report.md"}')
    print(f'[ok] 原始结果: {out_dir / "results.json"}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='真实内容质量评测（转录引擎 + 摘要模型横评）')
    parser.add_argument('--samples', required=True, help='样本清单 JSON 路径')
    parser.add_argument('--engines', default='', help='逗号分隔的转录引擎（firered,local,cloud_free,cloud）')
    parser.add_argument('--models', default='', help='逗号分隔的摘要 LLM 模型 key')
    parser.add_argument('--model-size', default='base', help='local(Whisper) 档位（默认 base）')
    parser.add_argument('--use-cache', action='store_true', help='允许复用转录/AI 缓存（默认全新识别）')
    parser.add_argument('--out', default=str(REPO_ROOT / 'eval' / 'reports' /
                                              datetime.now().strftime('%Y%m%d-%H%M%S')),
                        help='报告输出目录')
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f'[error] {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
