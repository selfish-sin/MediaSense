"""
prompt_builder 模块化提示词引擎测试
"""
from unittest.mock import patch, MagicMock

from services.prompt_builder import (
    PROMPT_DIMENSIONS, RECIPES,
    get_default_dimensions, _load_module_overrides, _get_module_template,
    get_dimensions_with_overrides, _get_tone_desc,
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
        assert d["language"] == "简体中文"
        assert d["speech_fix"] == "balanced"


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

    def test_recipes_pin_speech_fix_balanced(self):
        """内置配方统一钉口误纠错=按上下文纠错：口误残留是常态问题，配方应用即启用"""
        for r in RECIPES:
            assert r["dimensions"].get("speech_fix") == "balanced", r["key"]

    def test_process_inventory_recipe(self):
        """过程盘点预设：记录员人设 + 描述性规则 + 过程盘点结构；不挂任何评价型模块"""
        r = next(x for x in RECIPES if x["key"] == "process_inventory")
        assert r["dimensions"]["role"] == "recorder"
        assert r["dimensions"]["rules"] == "descriptive"
        assert r["dimensions"]["output"] == "process_ledger"
        # 不注入认知框架（mode）与类型预设（content_type）空模板，避免引入额外判断口径
        assert r["dimensions"]["mode"] == "general"
        assert r["dimensions"]["content_type"] == "general"
        assert r["dimensions"]["tone"] == "新闻客观"

    def test_descriptive_rule_allows_reasons_bans_judgement(self):
        """用户要求（2026-10-10）：可以分析并说明「为什么会这么说」，但不评述
        两条边界必须同时写死：理由还原有授权、好坏对错有禁令"""
        tpl = _get_module_template("rules", "descriptive")
        # 授权侧：理由、前提、推进关系、暗示
        for kw in ("为什么这么说", "理由", "前提", "推进关系", "暗示"):
            assert kw in tpl, kw
        # 禁令侧：评价、外部标尺
        assert "不做评价" in tpl
        assert "不引外部标尺" in tpl
        # 关键：不许讲理由 ≠ 不许讲评价，两条不能互相吞掉
        assert "不等于不许讲理由" in tpl
        # 正向加工范围写清，否则「不评述」会退化成什么都不做
        for kw in ("整理", "简化"):
            assert kw in tpl

    def test_process_ledger_output_has_no_evaluation_slot(self):
        """过程盘点结构：留「依据与为什么这么说」的位置，且不设评价栏"""
        tpl = _get_module_template("output", "process_ledger")
        assert "依据与为什么这么说" in tpl
        assert "不设评价" in tpl
        # 只拦「作为输出栏位的评价标题」；禁令声明句里点名这些词是必要的
        for banned in ("## 整体评价", "## 优点", "## 不足", "## 改进建议"):
            assert banned not in tpl, banned

    def test_process_inventory_prompt_builds_clean(self):
        """装配后：记录员人设与描述性纪律落地，且全文不出现评价栏"""
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            r = next(x for x in RECIPES if x["key"] == "process_inventory")
            prompt = build_modular_prompt(r["dimensions"], "素材内容")
        assert "忠实记录员" in prompt
        assert "只描述·不评述" not in prompt  # 模块名不进正文，只进模板正文
        assert "为什么这么说" in prompt
        assert "过程盘点" in prompt
        assert "整体评价" not in prompt

    def test_insight_digest_recipe(self):
        """观点提炼预设：欣赏型人设 + 肯定为主规则 + 观点梳理结构；不挂任何批判型模块"""
        r = next(x for x in RECIPES if x["key"] == "insight_digest")
        assert r["dimensions"]["role"] == "appreciator"
        assert r["dimensions"]["rules"] == "affirmative"
        assert r["dimensions"]["output"] == "viewpoints"
        # 不注入认知框架（mode）与类型预设（content_type）空模板，避免引入额外判断口径
        assert r["dimensions"]["mode"] == "general"
        assert r["dimensions"]["content_type"] == "general"
        # 篇幅克制：standard 档（3000 字），避免触发 ai_process 分批（分批=串行长请求，无汇总阶段）
        assert r["dimensions"]["depth"] == "standard"
        assert "critical" not in r["dimensions"].values()

    def test_affirmative_rule_allows_praise_and_natural_limits(self):
        """用户要求（2026-10-10）：只有正面、但自然带出问题，不质疑不并列
        两条边界必须同时写死：认可有授权且要具体、正反并列/裁决有禁令"""
        tpl = _get_module_template("rules", "affirmative")
        # 授权侧：认可讲透 + 限制顺带一提
        for kw in ("以认可为主", "值得取", "顺带一提", "不超过五处"):
            assert kw in tpl, kw
        # 禁令侧：不搞正反并列、不裁决
        for kw in ("不搞正反并列", "不下", "打分评级"):
            assert kw in tpl, kw
        # 关键：肯定与提醒互不吞掉（肯定不空洞、提醒不升级为审判）
        assert "两条互不吞掉" in tpl
        assert "不评价讲者本人" in tpl

    def test_viewpoints_output_has_conditional_reminder_slot(self):
        """观点梳理结构：留「值得取的地方」与「用时留意」的位置，且不设正反对照栏"""
        tpl = _get_module_template("output", "viewpoints")
        assert "值得取的地方" in tpl
        assert "用时留意" in tpl
        # 提醒是可选栏位、明确写了没必要就不写
        assert "没必要就不写" in tpl
        # 只拦「作为输出栏位的评价标题」；禁令声明句里点名这些词是必要的
        for banned in ("## 整体评价", "## 优点", "## 不足", "## 改进建议", "## 缺陷"):
            assert banned not in tpl, banned

    def test_insight_digest_prompt_builds_clean(self):
        """装配后：欣赏型人设与肯定纪律落地，且全文不出现正反评价栏"""
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            r = next(x for x in RECIPES if x["key"] == "insight_digest")
            prompt = build_modular_prompt(r["dimensions"], "素材内容")
        assert "善意而有分寸的整理者" in prompt
        assert "以认可为主" in prompt
        assert "值得取的地方" in prompt
        assert "用时留意" in prompt
        assert "整体评价" not in prompt
        # 批判型模块的措辞不得混入
        assert "逻辑谬误" not in prompt
        assert "指出内容的亮点和不足" not in prompt

    def test_tcm_recipe_and_role_module(self):
        """中医预设：专用角色模块 + 原貌保留与辨析补注型维度组合"""
        r = next(x for x in RECIPES if x["key"] == "tcm_lecture")
        assert r["dimensions"]["role"] == "tcm"
        assert r["dimensions"]["mode"] == "tcm"
        assert r["dimensions"]["content_type"] == "lecture"
        role_mods = [m["key"] for m in PROMPT_DIMENSIONS["role"]["modules"]]
        assert "tcm" in role_mods
        tcm_tpl = _get_module_template("role", "tcm")
        assert "术语原貌" in tcm_tpl
        assert "剂量" in tcm_tpl
        assert "辨析" in tcm_tpl
        assert "补注" in tcm_tpl

    def test_tcm_discern_merged_no_separate_recipe(self):
        """医理辨析已融入中医讲堂：单一配方、单一 tcm 角色/认知模块，无对比表格、无独立辨析配方"""
        assert not any(x["key"] == "tcm_discern" for x in RECIPES)
        r = next(x for x in RECIPES if x["key"] == "tcm_lecture")
        assert r["dimensions"]["output"] == "chapters"
        assert r["dimensions"]["rules"] == "deep"
        role_mods = [m["key"] for m in PROMPT_DIMENSIONS["role"]["modules"]]
        mode_mods = [m["key"] for m in PROMPT_DIMENSIONS["mode"]["modules"]]
        assert "tcm_analyst" not in role_mods
        assert "tcm_discern" not in mode_mods
        mode_tpl = _get_module_template("mode", "tcm")
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


class TestToneDesc:
    def test_get_tone_desc_known(self):
        assert "学术严谨" in _get_tone_desc("学术严谨")

    def test_get_tone_desc_unknown(self):
        assert "科幻风" in _get_tone_desc("科幻风")


class TestDepthHoldsWordBudget:
    """2026-10-11：word_count 维度并入 depth——篇幅只由 depth 一处决定，不得再有第二个旋钮"""

    def test_word_count_dimension_removed(self):
        assert "word_count" not in PROMPT_DIMENSIONS
        assert "word_count" not in get_default_dimensions()

    def test_each_depth_module_declares_word_budget(self):
        for m in PROMPT_DIMENSIONS["depth"]["modules"]:
            tpl = m.get("template") or ""
            assert any(k in tpl for k in ("字", "篇幅")), m["key"]

    def test_depth_budgets_match_old_word_count_tiers(self):
        """quick/standard/deep/research 分别承接原 500 / 3000 / 10000 / 不限 四档"""
        tpls = {m["key"]: m["template"] for m in PROMPT_DIMENSIONS["depth"]["modules"]}
        assert "500 字以内" in tpls["quick"]
        assert "3000 字" in tpls["standard"]
        assert "10000 字以上" in tpls["deep"]
        assert "篇幅不设上限" in tpls["research"]

    def test_recipes_carry_no_word_count_key(self):
        for r in RECIPES:
            assert "word_count" not in r["dimensions"], r["key"]

    def test_no_conflicting_budget_in_prompt(self):
        """回归：depth=quick 时提示词里不得再出现"至少3000字"这类冲突要求"""
        dims = get_default_dimensions()
        dims["depth"] = "quick"
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            prompt = build_modular_prompt(dims, "素材内容")
        assert "500 字以内" in prompt
        assert "宁多勿少" not in prompt
        assert "至少3000字" not in prompt


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


class TestSpeechFix:
    def test_default_prompt_contains_discipline(self):
        """默认含转录纠错纪律（单模块维度默认启用）：位置在认知规则之后、深度之前"""
        prompt = build_modular_prompt({}, "素材内容")
        assert "【转录纠错纪律】" in prompt
        # 拼装顺序：规则（【分析原则】）→ 纠错纪律 → 深度（【深度要求】）→ 内容
        assert prompt.index("【转录纠错纪律】") > prompt.index("【分析原则】")
        assert prompt.index("【转录纠错纪律】") < prompt.index("【深度要求】")
        assert prompt.index("【转录纠错纪律】") < prompt.index("【待处理内容】")

    def test_unknown_value_omits_section(self):
        """历史任务/显式空值优雅降级：不写纠错纪律块"""
        prompt = build_modular_prompt({"speech_fix": ""}, "素材内容")
        assert "【转录纠错纪律】" not in prompt

    def test_override_text_lands_in_prompt(self):
        """模板中心覆盖 speech_fix 模块文本后，拼装走覆盖文本"""
        with patch(
            "services.prompt_builder._load_module_overrides",
            return_value={"speech_fix": {"balanced": "【自定义纠错】\n测试覆盖文本"}},
        ):
            prompt = build_modular_prompt({}, "素材内容")
            assert "【自定义纠错】" in prompt
            assert "【转录纠错纪律】" not in prompt

    def test_tcm_quote_verbatim_not_conflicting(self):
        """纠错纪律明确豁免直接引语/经典条文：与 tcm 引文分级纪律可共存"""
        sf = _get_module_template("speech_fix", "balanced")
        assert "逐字保留" in sf

    def test_discipline_and_context_required(self):
        """用户要求：纠错判定 = 学科术语体系 + 上下文语境 结合，缺一不改"""
        sf = _get_module_template("speech_fix", "balanced")
        assert "学科术语体系" in sf
        assert "上下文语境" in sf
        assert "缺一" in sf
        assert "通行写法" in sf
        assert "通顺化" in sf

    def test_module_name_discipline_and_context(self):
        """模块名与描述体现「按学科与上下文纠错」"""
        m = PROMPT_DIMENSIONS["speech_fix"]["modules"][0]
        assert m["key"] == "balanced"
        assert m["name"] == "按学科与上下文纠错"
