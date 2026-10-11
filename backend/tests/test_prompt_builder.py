"""
prompt_builder 模块化提示词引擎测试
"""
from unittest.mock import patch, MagicMock

from services.prompt_builder import (
    PROMPT_DIMENSIONS, RECIPES, DIMENSION_ORDER,
    get_default_dimensions, _load_module_overrides, _get_module_template,
    get_dimensions_with_overrides, _get_tone_desc, _get_word_count_desc,
    build_modular_prompt, dimensions_to_legacy_summary_type,
)


class TestDimensions:
    def test_has_9_dims(self):
        """2026-10-11：删「输出语言」维度后 10 → 9（语言固定简体中文，纪律硬编码进 prompt）"""
        assert len(PROMPT_DIMENSIONS) == 9

    def test_dimension_order_is_declared_order(self):
        """展示顺序 = DIMENSION_ORDER（前端 DIMENSION_KEYS 与之对齐）；
        注意它只决定展示顺序，**不影响 build_modular_prompt 的段序**"""
        assert list(PROMPT_DIMENSIONS) == DIMENSION_ORDER
        assert DIMENSION_ORDER == [
            "role", "content_type", "mode", "rules",
            "depth", "output", "tone", "word_count", "speech_fix",
        ]

    def test_language_dimension_removed(self):
        """输出语言不再是可配置维度：外文素材也固定中文呈现"""
        assert "language" not in PROMPT_DIMENSIONS

    def test_dimension_labels_renamed(self):
        """改名说清用途：认知规则→分析立场、输出格式→输出形态（维度 key 不变，历史数据兼容）"""
        assert PROMPT_DIMENSIONS["rules"]["label"] == "分析立场"
        assert PROMPT_DIMENSIONS["output"]["label"] == "输出形态"

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
        assert d["speech_fix"] == "balanced"
        assert "language" not in d


class TestRecipes:
    def test_recipes_not_empty(self):
        assert len(RECIPES) > 0

    def test_recipes_ordered_by_real_usage(self):
        """配方顺序 = 历史真实使用频次降序（2026-10-11 按 340 个任务统计重排）
        统计口径：忽略 language/speech_fix（后加的恒定默认值，历史任务常缺键），其余维度全等；
        手搭的同家族变体（如 research 深度 + 学术批判框架）归并到最接近的配方。
        实测频次：通用默认 146 / 深度批判 43(+30 手搭) / 中医 24(+17) / 课程 31 / 其余 ≤1。
        最高频的默认组合原先没有任何配方入口（配方栏第 1 位是仅用 1 次的速看），
        故新增 general_summary 代表该默认档并置于首位。"""
        assert [r["key"] for r in RECIPES] == [
            "general_summary",     # 146 次：默认通用总结（原先无入口）
            "deep_research",       # 43 次 + 30 次手搭变体
            "tcm_lecture",         # 24 次 + 17 次手搭变体
            "course_notes",        # 31 次手搭近似组合
            "quick_watch",         # 1 次
            "process_inventory",   # 1 次
            "insight_digest",      # 0 次但为用户明确需求，排 0 组首位
            "news_brief",
            "tutorial_steps",
            "talk_digest",
            "reality_briefing",    # 2026-10-11 用户明确需求：现实研判（智库式），追加在末尾
        ]

    def test_general_summary_recipe_is_app_default(self):
        """通用总结配方 = 应用默认维度组合（用户最高频的实际用法）"""
        assert next(r for r in RECIPES if r["key"] == "general_summary")["dimensions"] \
            == get_default_dimensions()

    def test_recipe_names_are_self_explanatory(self):
        """配方名要能看出产出什么：带 emoji 前缀 + 关键限定词，长度不强制四字"""
        for r in RECIPES:
            assert r["name"].split(" ", 1)[1], r["key"]  # emoji 后必须有中文名
            assert len(r["desc"]) >= 10, r["key"]          # 描述要说清适用场景

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

    def test_recipes_no_language_key(self):
        """输出语言已不是维度：配方里不得再出现 language 键（否则又变成可配的）"""
        for r in RECIPES:
            assert "language" not in r["dimensions"], r["key"]

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
        # 篇幅克制：standard + 3000 字，避免触发 ai_process 分批（分批=串行长请求，无汇总阶段）
        assert r["dimensions"]["depth"] == "standard"
        assert r["dimensions"]["word_count"] == "3000"
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
            assert list(result) == DIMENSION_ORDER

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


# ============ 输出语言：2026-10-11 起固定简体中文，不再是可配置维度 ============


class TestOutputLanguageFixed:
    def test_default_prompt_contains_chinese_discipline(self):
        """默认拼装必带简体中文纪律，位置在语气之后（与删除维度前逐字节一致）"""
        prompt = build_modular_prompt({}, "素材内容")
        assert "输出语言：" in prompt
        assert "简体中文" in prompt
        assert prompt.index("输出语言：") > prompt.index("语气：")

    def test_language_key_from_history_is_ignored(self):
        """历史任务的 language 键被忽略：改不成英语，仍固定简体中文"""
        prompt = build_modular_prompt({"language": "英语"}, "素材内容")
        assert "输出一律使用英语" not in prompt
        assert "输出语言：" in prompt
        assert "简体中文" in prompt

    def test_full_override_no_language_block(self):
        """full_override 完全接管提示词，无选项块"""
        prompt = build_modular_prompt({}, "素材内容", full_override="逐字记录 {{CONTENT}}")
        assert "输出语言：" not in prompt


class TestPromptTextRestored:
    """2026-10-11 回退锁：用户要求提示词正文与档位保持原样、不做合并。
    本类断言就是"别再擅自改提示词"的红线——确需改动时必须先说明需求并同步更新本类。
    到目前为止仅两处经用户逐条授权的正文改动：①输出模块段落标题同步为【输出形态】；
    ②depth 四档去掉写死的字数（避免与「字数」维度冲突）。"""

    def test_all_baseline_modules_present(self):
        """曾误删的 13 个档位必须都在（角色 2 / 内容类型 6 / 输出 3 / 语气 2）
        用子集断言：新增档位不影响本锁，但删档位会立刻报红"""
        expected = {
            "role": {"general", "appreciator", "recorder", "academic",
                     "tech", "journalist", "creative", "tcm"},
            "content_type": {"general", "tutorial", "news", "interview", "lecture",
                             "entertainment", "academic_study", "argument",
                             "event", "storytelling"},
            "output": {"summary", "bullets", "qa", "timeline", "chapters",
                       "process_ledger", "viewpoints", "comparison", "freeform"},
            "tone": {"学术严谨", "通俗科普", "口语化", "新闻客观", "正式商务"},
            "mode": {"general", "social_science", "tcm"},
        }
        for dim_key, keys in expected.items():
            actual = {m["key"] for m in PROMPT_DIMENSIONS[dim_key]["modules"]}
            assert keys <= actual, f"{dim_key} 有档位被删：{keys - actual}"

    def test_depth_has_no_hardcoded_word_counts(self):
        """depth 只管分析粒度，不写死字数（2026-10-11 用户要求：模板里写死字数会与「字数」维度
        的真实下限冲突，篇幅一律由 word_count 维度控制）"""
        banned = ("500字", "500 字", "3000字", "3000 字", "10000字", "10000 字", "800-1500")
        for m in PROMPT_DIMENSIONS["depth"]["modules"]:
            blob = m["desc"] + m["template"]
            for b in banned:
                assert b not in blob, f"depth={m['key']} 仍写死字数：{b}"

    def test_deep_research_recipe_uses_chapters(self):
        """「🧠 深度批判分析」输出形态 = 章节分析（2026-10-11 用户拍板的配方层调整，未动提示词正文）"""
        r = next(x for x in RECIPES if x["key"] == "deep_research")
        assert r["dimensions"]["output"] == "chapters"
        assert r["dimensions"]["rules"] == "critical"
        assert r["dimensions"]["depth"] == "deep"

    def test_output_section_headers_match_dimension_name(self):
        """输出模块的段落标题与维度展示名一致（2026-10-11 用户明确授权同步为【输出形态】；
        此前正文仍是【输出格式】，是"只改展示名未动正文"的过渡状态）"""
        for key in ("summary", "bullets", "qa", "timeline", "chapters",
                    "process_ledger", "viewpoints", "comparison", "freeform"):
            assert _get_module_template("output", key).startswith("【输出形态】"), key

    def test_summary_not_merged_with_bullets(self):
        """「要点列表」独立存在，摘要模板里没有那段合并说明"""
        tpl = _get_module_template("output", "summary")
        assert "短列表" not in tpl
        assert "## 核心主题" in tpl
        assert "以要点列表为主" in _get_module_template("output", "bullets")


class TestSpeechFix:
    def test_default_prompt_contains_discipline(self):
        """默认含转录纠错纪律（单模块维度默认启用）：位置在「分析立场」之后、深度之前"""
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


# ============ 现实研判（2026-10-11 新增：认知模式 + 角色 + 配方） ============


class TestRealityBriefing:
    """用户需求原话：要的是「认识方法与重点」——点出内容在当下的价值与紧迫性、
    指出它的理论特色与视角遮蔽、考究其论证合理性；不要写成理论综述，也不要去做事实核查。
    定位类比：像智库做研判与辅助研判。"""

    def test_mode_module_exists(self):
        """认知模式新增 reality 档位，与「社科深度特化」平级"""
        mods = [m["key"] for m in PROMPT_DIMENSIONS["mode"]["modules"]]
        assert "reality" in mods
        assert PROMPT_DIMENSIONS["mode"]["modules"][mods.index("reality")]["name"] == "现实研判"

    def test_mode_template_covers_key_points(self):
        """正文必须覆盖：视角 → 遮蔽 → 当下形势 → 研判与推理考究 → 不做事实核查 → 层次跟随"""
        tpl = _get_module_template("mode", "reality")
        for kw in ("观察视角", "遮蔽", "当下的现实情况", "研判与辅助研判",
                   "推理有没有跳步", "不做事实核查", "层次跟随内容"):
            assert kw in tpl, kw
        # 关键边界：不许把内容硬拽到理论坐标上
        assert "不要往理论坐标上拽" in tpl
        # 不是学术综述、不是事实核查
        assert "不是写学术综述" in tpl

    def test_role_module_exists(self):
        """角色新增「智库分析师」，与研判定位配套"""
        mods = {m["key"]: m for m in PROMPT_DIMENSIONS["role"]["modules"]}
        assert "analyst" in mods
        assert mods["analyst"]["name"] == "智库分析师"
        assert "辅助研判" in mods["analyst"]["template"]

    def test_recipe_combines_mode_and_role(self):
        """预设配方「🎯 现实研判」：角色=智库分析师 + 认知模式=现实研判 + 章节分析"""
        r = next(x for x in RECIPES if x["key"] == "reality_briefing")
        assert r["dimensions"]["role"] == "analyst"
        assert r["dimensions"]["mode"] == "reality"
        assert r["dimensions"]["output"] == "chapters"
        assert r["dimensions"]["depth"] == "deep"
        # 分析立场必须是 deep：standard 含"不添加视频中未提及的信息"，会与 reality 的 [延伸] 授权冲突
        assert r["dimensions"]["rules"] == "deep"
        # 选到它时提示词里必须真的注入研判框架（不是空模板）
        with patch("services.prompt_builder._load_module_overrides", return_value={}):
            prompt = build_modular_prompt(r["dimensions"], "素材内容")
        assert "【现实研判·认知框架】" in prompt
        assert "智库分析师" in prompt
        assert "素材内容" in prompt
