"""
prompt_builder 模块化提示词引擎测试
"""
import pytest
from unittest.mock import patch, MagicMock

from services.prompt_builder import (
    PROMPT_DIMENSIONS, RECIPES,
    get_default_dimensions, _load_module_overrides, _get_module_template,
    get_dimensions_with_overrides, _get_tone_desc, _get_word_count_desc,
    build_modular_prompt, dimensions_to_legacy_summary_type,
)


class TestDimensions:
    def test_has_9_dims(self):
        assert len(PROMPT_DIMENSIONS) == 9

    def test_each_dim_has_required_fields(self):
        for dim_key, dim_cfg in PROMPT_DIMENSIONS.items():
            assert "label" in dim_cfg
            assert "desc" in dim_cfg
            assert "default" in dim_cfg
            assert "modules" in dim_cfg
            assert len(dim_cfg["modules"]) > 0

    def test_each_module_has_key_name_desc(self):
        for dim_key, dim_cfg in PROMPT_DIMENSIONS.items():
            for m in dim_cfg["modules"]:
                assert "key" in m
                assert "name" in m
                assert "desc" in m

    def test_get_default_dimensions(self):
        defaults = get_default_dimensions()
        assert len(defaults) == 9
        for dim_key, dim_cfg in PROMPT_DIMENSIONS.items():
            assert defaults[dim_key] == dim_cfg["default"]

    def test_default_dimensions_values(self):
        d = get_default_dimensions()
        assert d["role"] == "general"
        assert d["mode"] == "general"
        assert d["rules"] == "standard"
        assert d["output"] == "summary"
        assert d["depth"] == "standard"
        assert d["tone"] == "学术严谨"
        assert d["word_count"] == "3000"
        assert d["language"] == "简体中文"


class TestRecipes:
    def test_recipes_not_empty(self):
        assert len(RECIPES) > 0

    def test_each_recipe_has_dimensions(self):
        for r in RECIPES:
            assert "key" in r
            assert "name" in r
            assert "dimensions" in r
            assert len(r["dimensions"]) == 9

    def test_recipe_dimensions_valid(self):
        for r in RECIPES:
            for dim_key, val in r["dimensions"].items():
                assert dim_key in PROMPT_DIMENSIONS
                valid_keys = [m["key"] for m in PROMPT_DIMENSIONS[dim_key]["modules"]]
                assert val in valid_keys, f"Recipe {r['key']}: {dim_key}={val} not in {valid_keys}"

    def test_recipes_pin_language_simplified_chinese(self):
        """内置配方统一钉输出语言=简体中文：应用配方即回到默认简中"""
        for r in RECIPES:
            assert r["dimensions"].get("language") == "简体中文", r["key"]

    def test_tcm_recipe_and_role_module(self):
        """中医预设：专用角色模块 + 原貌保留型维度组合"""
        r = next(x for x in RECIPES if x["key"] == "tcm_lecture")
        assert r["dimensions"]["role"] == "tcm"
        assert r["dimensions"]["mode"] == "tcm"
        assert r["dimensions"]["content_type"] == "lecture"
        role_mods = [m["key"] for m in PROMPT_DIMENSIONS["role"]["modules"]]
        assert "tcm" in role_mods
        tcm_tpl = _get_module_template("role", "tcm")
        assert "术语原貌" in tcm_tpl
        assert "剂量" in tcm_tpl

    def test_tcm_discern_recipe_and_modules(self):
        """医理辨析预设：辨析者角色 + 辨析特化模式 + 对比表格输出，补注不外加内容"""
        r = next(x for x in RECIPES if x["key"] == "tcm_discern")
        assert r["dimensions"]["role"] == "tcm_analyst"
        assert r["dimensions"]["mode"] == "tcm_discern"
        assert r["dimensions"]["output"] == "comparison"
        role_tpl = _get_module_template("role", "tcm_analyst")
        assert "辨析" in role_tpl
        assert "补注" in role_tpl
        mode_tpl = _get_module_template("mode", "tcm_discern")
        assert "补注纪律" in mode_tpl
        assert "不下定论" in mode_tpl

    def test_tcm_mode_module(self):
        """中医认知模式：mode=tcm 模块存在，选中时框架注入提示词"""
        mode_mods = [m["key"] for m in PROMPT_DIMENSIONS["mode"]["modules"]]
        assert "tcm" in mode_mods
        tpl = _get_module_template("mode", "tcm")
        assert "引文分级" in tpl
        assert "辨证链" in tpl
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            prompt = build_modular_prompt({"mode": "tcm"}, "content")
            assert "【中医特化·认知框架】" in prompt


class TestModuleTemplate:
    def test_get_module_template_default(self):
        tpl = _get_module_template("role", "general")
        assert "分析助手" in tpl

    def test_get_module_template_empty(self):
        tpl = _get_module_template("content_type", "general")
        assert tpl == ""

    def test_get_module_template_not_found(self):
        tpl = _get_module_template("role", "nonexistent")
        assert tpl == ""

    def test_get_module_template_with_overrides(self):
        overrides = {"role": {"general": "自定义角色文本"}}
        tpl = _get_module_template("role", "general", overrides)
        assert tpl == "自定义角色文本"

    def test_get_module_template_override_not_match(self):
        overrides = {"role": {"other": "text"}}
        tpl = _get_module_template("role", "general", overrides)
        assert "分析助手" in tpl


class TestLoadOverrides:
    def test_load_overrides_db_error(self):
        with patch("db.database.get_connection", side_effect=Exception("no db")):
            result = _load_module_overrides()
            assert result == {}

    def test_load_overrides_success(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            {"dimension_key": "role", "module_key": "general", "custom_text": "覆盖文本"},
        ]
        with patch("db.database.get_connection", return_value=mock_conn):
            result = _load_module_overrides()
            assert result == {"role": {"general": "覆盖文本"}}
        mock_conn.close.assert_called_once()


class TestGetDimensionsWithOverrides:
    @staticmethod
    def _row(dim, mod, text, display_name=""):
        return {"dimension_key": dim, "module_key": mod, "custom_text": text, "display_name": display_name}

    def test_returns_all_dims(self):
        with patch("services.prompt_builder._load_override_rows", return_value=[]):
            result = get_dimensions_with_overrides()
            assert len(result) == 9

    def test_marks_customized(self):
        with patch("services.prompt_builder._load_override_rows",
                   return_value=[self._row("role", "general", "自定义")]):
            result = get_dimensions_with_overrides()
            general_mod = [m for m in result["role"]["modules"] if m["key"] == "general"][0]
            assert general_mod["is_customized"] is True
            assert general_mod["template"] == "自定义"

    def test_marks_not_customized(self):
        with patch("services.prompt_builder._load_override_rows", return_value=[]):
            result = get_dimensions_with_overrides()
            general_mod = [m for m in result["role"]["modules"] if m["key"] == "general"][0]
            assert general_mod["is_customized"] is False

    def test_appends_custom_modules(self):
        rows = [
            self._row("role", "my_role_a", "文本A", "我的角色A"),
            self._row("role", "my_role_b", "文本B", "我的角色B"),
        ]
        with patch("services.prompt_builder._load_override_rows", return_value=rows):
            result = get_dimensions_with_overrides()
        customs = [m for m in result["role"]["modules"] if m.get("is_custom")]
        assert [m["key"] for m in customs] == ["my_role_a", "my_role_b"]
        assert [m["name"] for m in customs] == ["我的角色A", "我的角色B"]
        assert all(m["is_customized"] for m in customs)
        assert customs[0]["template"] == "文本A"

    def test_custom_module_without_display_name_falls_back_to_key(self):
        with patch("services.prompt_builder._load_override_rows",
                   return_value=[self._row("rules", "my_rule", "文本")]):
            result = get_dimensions_with_overrides()
        custom = [m for m in result["rules"]["modules"] if m.get("is_custom")][0]
        assert custom["name"] == "my_rule"

    def test_override_of_builtin_not_duplicated_as_custom(self):
        rows = [self._row("role", "general", "覆盖内置", "不该出现的名字")]
        with patch("services.prompt_builder._load_override_rows", return_value=rows):
            result = get_dimensions_with_overrides()
        role_keys = [m["key"] for m in result["role"]["modules"]]
        assert role_keys.count("general") == 1
        assert not any(m.get("is_custom") for m in result["role"]["modules"])


class TestToneAndWordCount:
    def test_get_tone_desc_known(self):
        assert "学术严谨" in _get_tone_desc("学术严谨")

    def test_get_tone_desc_unknown(self):
        assert "科幻风" in _get_tone_desc("科幻风")

    def test_get_word_count_desc_known(self):
        desc = _get_word_count_desc("3000")
        assert "3000" in desc

    def test_get_word_count_desc_unlimited(self):
        assert _get_word_count_desc("不限") == ""

    def test_get_word_count_desc_unknown(self):
        desc = _get_word_count_desc("7000")
        assert "7000" in desc


class TestBuildModularPrompt:
    def test_full_override_with_placeholder(self):
        prompt = build_modular_prompt(
            {}, "test content",
            full_override="自定义提示词 {{CONTENT}} 结束"
        )
        assert "自定义提示词" in prompt
        assert "test content" in prompt
        assert "结束" in prompt

    def test_full_override_without_placeholder(self):
        prompt = build_modular_prompt(
            {}, "test content",
            full_override="完全自定义提示词"
        )
        assert "完全自定义提示词" in prompt
        assert "test content" in prompt
        assert "【待处理内容】" in prompt

    def test_full_override_whitespace_only(self):
        prompt = build_modular_prompt(
            {"role": "general"}, "content",
            full_override="   "
        )
        assert "分析助手" in prompt

    def test_default_dimensions(self):
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            prompt = build_modular_prompt({}, "test content")
            assert "test content" in prompt
            assert "【待处理内容】" in prompt

    def test_with_custom_dimensions(self):
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            prompt = build_modular_prompt(
                {"role": "academic", "output": "bullets", "depth": "quick"},
                "content here"
            )
            assert "研究员" in prompt
            assert "content here" in prompt

    def test_with_custom_prompt(self):
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            prompt = build_modular_prompt(
                {}, "content",
                custom_prompt="请特别关注XYZ"
            )
            assert "自定义指引" in prompt
            assert "请特别关注XYZ" in prompt

    def test_no_content(self):
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            prompt = build_modular_prompt({}, "")
            assert "{{CONTENT}}" in prompt

    def test_empty_template_skipped(self):
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            prompt = build_modular_prompt({"content_type": "general"}, "content")
            assert "content" in prompt


class TestDimensionsToLegacy:
    def test_empty_dimensions(self):
        assert dimensions_to_legacy_summary_type({}) == "modular"
        assert dimensions_to_legacy_summary_type(None) == "modular"

    def test_quick_depth(self):
        assert dimensions_to_legacy_summary_type({"depth": "quick"}) == "quick"

    def test_chapters_output(self):
        assert dimensions_to_legacy_summary_type({"output": "chapters"}) == "chapter"

    def test_qa_output(self):
        assert dimensions_to_legacy_summary_type({"output": "qa"}) == "qa"

    def test_mindmap_output(self):
        assert dimensions_to_legacy_summary_type({"output": "mindmap"}) == "mindmap"

    def test_timeline_output(self):
        assert dimensions_to_legacy_summary_type({"output": "timeline"}) == "timeline"

    def test_comparison_output(self):
        assert dimensions_to_legacy_summary_type({"output": "comparison"}) == "comparison"

    def test_default_summary(self):
        assert dimensions_to_legacy_summary_type({"output": "summary"}) == "summary"
        assert dimensions_to_legacy_summary_type({"depth": "standard"}) == "summary"


# ============ 输出语言维度（2026-09-12 删翻译功能后新增，替代原注入机制） ============


class TestLanguageOption:
    def test_default_prompt_contains_chinese(self):
        """默认输出语言=简体中文，拼在字数之后（外文素材→中文呈现）"""
        prompt = build_modular_prompt({}, "素材内容")
        assert "输出语言：" in prompt
        assert "简体中文" in prompt
        # 顺序：输出语言行在字数要求之后
        assert prompt.index("输出语言：") > prompt.index("语气：")

    def test_custom_language(self):
        """选择其他语言时提示词跟随变化"""
        prompt = build_modular_prompt({"language": "英语"}, "素材内容")
        assert "输出一律使用英语" in prompt

    def test_unknown_language_omits_line(self):
        """历史任务/未知值优雅降级：不写语言行"""
        prompt = build_modular_prompt({"language": "不存在"}, "素材内容")
        assert "输出语言：" not in prompt

    def test_full_override_no_language_block(self):
        """full_override 完全接管提示词，无选项块"""
        prompt = build_modular_prompt({}, "素材内容", full_override="逐字记录 {{CONTENT}}")
        assert "输出语言：" not in prompt
