"""
prompts 提示词模板库测试
"""
from services.prompts import (
    TONE_DESCS, FORMAT_DESCS, PURPOSE_DESCS, WORD_COUNT_DESCS,
    build_prompt_vars, SUMMARY_PROMPTS,
)


class TestPromptDescs:
    def test_tone_descs(self):
        assert "学术严谨" in TONE_DESCS
        assert "通俗科普" in TONE_DESCS
        assert "口语化" in TONE_DESCS
        assert "新闻客观" in TONE_DESCS
        assert "正式商务" in TONE_DESCS

    def test_format_descs(self):
        assert "Markdown" in FORMAT_DESCS
        assert "纯文本" in FORMAT_DESCS
        assert "结构化大纲" in FORMAT_DESCS
        assert "要点列表" in FORMAT_DESCS

    def test_purpose_descs(self):
        assert "学习笔记" in PURPOSE_DESCS
        assert "深度分析" in PURPOSE_DESCS
        assert "素材收集" in PURPOSE_DESCS

    def test_word_count_descs(self):
        assert "不限" in WORD_COUNT_DESCS
        assert "3000" in WORD_COUNT_DESCS
        assert "5000" in WORD_COUNT_DESCS
        assert WORD_COUNT_DESCS["不限"] == ""

    def test_summary_prompts_not_empty(self):
        assert len(SUMMARY_PROMPTS) > 0
        for key, prompt in SUMMARY_PROMPTS.items():
            assert isinstance(prompt, str)
            assert len(prompt) > 50


class TestBuildPromptVars:
    def test_default_values(self):
        result = build_prompt_vars({})
        assert result["{{TONE_DESC}}"] == TONE_DESCS["学术严谨"]
        assert result["{{WORD_COUNT_DESC}}"] == WORD_COUNT_DESCS["3000"]
        assert result["{{FORMAT_DESC}}"] == FORMAT_DESCS["Markdown"]
        assert result["{{PURPOSE_DESC}}"] == PURPOSE_DESCS["学习笔记"]
        assert result["{{OCR_NOTE}}"] == ""
        assert "标题生成规则" in result["{{TITLE_RULE}}"]

    def test_custom_tone(self):
        result = build_prompt_vars({"tone": "口语化"})
        assert result["{{TONE_DESC}}"] == TONE_DESCS["口语化"]

    def test_custom_word_count(self):
        result = build_prompt_vars({"word_count": "5000"})
        assert "5000" in result["{{WORD_COUNT_DESC}}"]

    def test_custom_format(self):
        result = build_prompt_vars({"format": "纯文本"})
        assert result["{{FORMAT_DESC}}"] == FORMAT_DESCS["纯文本"]

    def test_custom_purpose(self):
        result = build_prompt_vars({"purpose": "素材收集"})
        assert result["{{PURPOSE_DESC}}"] == PURPOSE_DESCS["素材收集"]

    def test_unknown_tone(self):
        result = build_prompt_vars({"tone": "科幻风"})
        assert "科幻风" in result["{{TONE_DESC}}"]

    def test_unknown_format(self):
        result = build_prompt_vars({"format": "LaTeX"})
        assert "LaTeX" in result["{{FORMAT_DESC}}"]

    def test_unknown_purpose(self):
        result = build_prompt_vars({"purpose": "翻译"})
        assert "翻译" in result["{{PURPOSE_DESC}}"]

    def test_custom_tone_value(self):
        result = build_prompt_vars({"tone": "自定义", "tone_custom": "自定义语气描述"})
        assert result["{{TONE_DESC}}"] == "自定义语气描述"

    def test_custom_word_count_value(self):
        result = build_prompt_vars({"word_count": "自定义", "word_count_custom": "至少8000字"})
        assert result["{{WORD_COUNT_DESC}}"] == "至少8000字"

    def test_custom_format_value(self):
        result = build_prompt_vars({"format": "自定义", "format_custom": "特殊格式"})
        assert result["{{FORMAT_DESC}}"] == "特殊格式"

    def test_custom_purpose_value(self):
        result = build_prompt_vars({"purpose": "自定义", "purpose_custom": "特殊目的"})
        assert result["{{PURPOSE_DESC}}"] == "特殊目的"

    def test_unknown_word_count(self):
        result = build_prompt_vars({"word_count": "7000"})
        # prompts.py 对未知字数返回空字符串（prompt_builder 的 _get_word_count_desc 才有回退）
        assert result["{{WORD_COUNT_DESC}}"] == ""

    def test_unlimited_word_count(self):
        result = build_prompt_vars({"word_count": "不限"})
        assert result["{{WORD_COUNT_DESC}}"] == ""

    def test_all_keys_present(self):
        result = build_prompt_vars({})
        assert "{{TONE_DESC}}" in result
        assert "{{WORD_COUNT_DESC}}" in result
        assert "{{FORMAT_DESC}}" in result
        assert "{{PURPOSE_DESC}}" in result
        assert "{{OCR_NOTE}}" in result
        assert "{{TITLE_RULE}}" in result
