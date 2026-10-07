"""eval_metrics 纯函数测试（真实内容质量评测指标，2026-10-07）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from eval_metrics import (
    cer, dose_numbers, format_term_report, metrics_dict,
    missing_numbers, normalize_zh, term_coverage, term_hits,
)


class TestNormalizeZh:
    def test_strips_punct_whitespace_and_case(self):
        assert normalize_zh('Hello， 世界！ GPT-4。') == 'hello世界gpt4'

    def test_fullwidth_to_halfwidth(self):
        assert normalize_zh('黄芪３０ｇ') == '黄芪30g'

    def test_empty(self):
        assert normalize_zh('') == ''
        assert normalize_zh(None) == ''


class TestCer:
    def test_identical_is_zero(self):
        ref = '黄芪味甘性微温，归脾肺经。'
        assert cer(ref, ref) == 0.0

    def test_completely_different_is_high(self):
        assert cer('今天天气很好', '丙丁戊己庚辛') > 0.8

    def test_partial_error(self):
        # 参考十几个字，识别错两三个字 → CER 在 0.1~0.4 之间
        ref = '足三里位于犊鼻下三寸'
        hyp = '足三里位于輆鼻下三寸'
        assert 0.0 < cer(ref, hyp) < 0.4

    def test_empty_reference_returns_sentinel(self):
        assert cer('', 'anything') == -1.0

    def test_empty_hypothesis_is_total_error(self):
        assert cer('参考文本', '') == 1.0

    def test_punct_insensitive(self):
        # 标点/空白差异不应计错
        assert cer('黄芪，白术，甘草。', '黄芪 白术 甘草') == 0.0


class TestTermHits:
    def test_hit_and_miss(self):
        text = '黄芪建中汤治疗脾胃虚寒，配合艾灸足三里。'
        hits = term_hits(text, ['黄芪', '足三里', '白术'])
        assert dict(hits) == {'黄芪': True, '足三里': True, '白术': False}

    def test_coverage_counts(self):
        hit, total = term_coverage('黄芪 白术', ['黄芪', '白术', '甘草'])
        assert (hit, total) == (2, 3)

    def test_empty_terms(self):
        assert term_coverage('任何文本', []) == (0, 0)

    def test_format_report_marks_miss(self):
        s = format_term_report('黄芪', ['黄芪', '甘草'])
        assert '黄芪✓' in s and '甘草✗' in s


class TestDoseNumbers:
    def test_extracts_number_with_unit(self):
        assert dose_numbers('黄芪30g，白术10 克，每日1剂，水煎400ml') == ['30g', '10 克', '1剂', '400ml']

    def test_ignores_bare_numbers(self):
        assert dose_numbers('第3章讲了5个方子') == []

    def test_empty(self):
        assert dose_numbers('') == []


class TestMissingNumbers:
    def test_hallucinated_dose_detected(self):
        transcript = '黄芪30g 白术10g'
        summary = '方中黄芪30g，另加当归15g'
        assert missing_numbers(summary, transcript) == ['15g']

    def test_faithful_summary_has_none(self):
        transcript = '黄芪30g 白术10g'
        summary = '黄芪用30g，白术10g'
        assert missing_numbers(summary, transcript) == []


class TestMetricsDict:
    def test_full_metrics(self):
        m = metrics_dict(
            '黄芪味甘，用量30g。', '黄芪味甘，用量30g。',
            terms=['黄芪', '甘草'], dosages=['30g'])
        assert m['cer'] == 0.0
        assert m['terms_hit'] == '1/2'
        assert m['terms_miss'] == ['甘草']
        assert m['dosages_hit'] == '1/1'
        assert m['ref_len'] is not None

    def test_no_reference_cer_is_none(self):
        m = metrics_dict('', '识别文本', terms=[], dosages=[])
        assert m['cer'] is None

    def test_short_reference_cer_nan_guard(self):
        # 参考为纯标点 → 归一化后为空 → cer 返回哨兵，metrics 里为 None
        m = metrics_dict('。，！', '文本', terms=[], dosages=[])
        assert m['cer'] is None
