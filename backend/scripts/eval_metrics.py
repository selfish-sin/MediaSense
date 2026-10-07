'''评测指标纯函数（真实内容质量评测，2026-10-07）。

被 eval_quality.py 消费；独立成模块便于单测。中文取向：
- CER 用字级相似度近似（difflib），参考文本与识别文本先做归一化（去空白/标点/大小写）。
- 术语/剂量命中按归一化后子串匹配；"无出处数字"是摘要幻觉的代理指标。
'''
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Tuple

# 剂量/数量的数字+单位片段（g/克/mg/ml/毫升/付/剂/枚/钱/两/分钟/小时）
_NUM_UNIT_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:mg|ml|g|克|毫升|付|剂|枚|钱|两)')

# 归一化时剔除的标点/符号（中英文常见）
_PUNCT_RE = re.compile(
    r'[\s，。！？；：、""\'\'（）《》\[\]【】…—·\-_,.!?:;()"\'<>%\\/*＆&#@~`^+=|$]+')


def normalize_zh(text: str) -> str:
    """中文评测归一化：全角→半角、去标点空白、转小写。保留数字与单位字母。"""
    if not text:
        return ''
    text = unicodedata.normalize('NFKC', text)
    return _PUNCT_RE.sub('', text).lower()


def cer(reference: str, hypothesis: str) -> float:
    """字错误率近似：1 - 相似度（归一化后）。参考文本为空返回 nan 语义（-1 由调用方处理）。"""
    ref_n = normalize_zh(reference)
    hyp_n = normalize_zh(hypothesis)
    if not ref_n:
        return -1.0
    if not hyp_n:
        return 1.0
    ratio = SequenceMatcher(None, ref_n, hyp_n).ratio()
    return max(0.0, 1.0 - ratio)


def term_hits(text: str, terms: List[str]) -> List[Tuple[str, bool]]:
    """逐术语判定是否出现在文本中（归一化子串匹配）。"""
    text_n = normalize_zh(text)
    return [(term, (normalize_zh(term) in text_n)) for term in terms or []]


def term_coverage(text: str, terms: List[str]) -> Tuple[int, int]:
    """(命中数, 总数)。"""
    hits = term_hits(text, terms)
    return sum(1 for _, ok in hits if ok), len(hits)


def format_term_report(text: str, terms: List[str]) -> str:
    """人读的术语命中明细：'黄芪✓ 白术✗'。"""
    return ' '.join(f"{term}{'✓' if ok else '✗'}" for term, ok in term_hits(text, terms))


def dose_numbers(text: str) -> List[str]:
    """提取文本中「数字+单位」片段（剂量保留/幻觉检查的基础件）。"""
    return [m.group(0).strip() for m in _NUM_UNIT_RE.finditer(text or '')]


def missing_numbers(summary: str, transcript: str) -> List[str]:
    """摘要里出现、转录原文中没有的「数字+单位」——摘要幻觉/编造剂量的代理指标。"""
    src = set(dose_numbers(transcript))
    out = []
    for n in dose_numbers(summary):
        if n not in src:
            out.append(n)
    return out


def metrics_dict(reference: str, hypothesis: str, terms: List[str],
                 dosages: List[str]) -> Dict[str, object]:
    """一次算齐一组转录评测指标（CER 需要参考文本；无参考时为 None）。"""
    char_err = cer(reference, hypothesis) if reference else None
    t_hit, t_total = term_coverage(hypothesis, terms)
    d_hit, d_total = term_coverage(hypothesis, dosages)
    return {
        'cer': round(char_err, 4) if char_err is not None and char_err >= 0 else None,
        'char_len': len(normalize_zh(hypothesis)),
        'ref_len': len(normalize_zh(reference)) if reference else None,
        'terms_hit': f'{t_hit}/{t_total}',
        'terms_miss': [t for t, ok in term_hits(hypothesis, terms) if not ok],
        'dosages_hit': f'{d_hit}/{d_total}',
        'dosages_miss': [d for d, ok in term_hits(hypothesis, dosages) if not ok],
    }
