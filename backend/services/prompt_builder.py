"""
MediaSense - 模块化提示词构建引擎
9 维度可组合：角色 + 认知模式 + 内容类型 + 认知规则 + 深度 + 输出格式 + 语气 + 字数 + 输出语言
支持 DB 覆盖（prompt_module_overrides 表）和完全自定义（full_override）
"""
from typing import Dict, List, Optional

# ============ 维度元数据（供前端渲染选择器） ============
# 每个维度包含若干模块，每个模块: {key, name, desc, template}

PROMPT_DIMENSIONS = {
    # ────────────── 1. 角色 ──────────────
    "role": {
        "label": "角色",
        "desc": "AI 扮演的分析师人设，决定视角和专长",
        "default": "general",
        "modules": [
            {
                "key": "general",
                "name": "通用助手",
                "desc": "均衡适配，适合大多数视频",
                "template": "你是一位经验丰富的内容分析助手。你的任务是准确理解视频内容，提炼关键信息，用清晰有条理的方式呈现给用户。你擅长快速抓住重点，不遗漏重要细节，也不过度解读。",
            },
            {
                "key": "recorder",
                "name": "忠实记录员",
                "desc": "只记录视频内发生了什么，不评判好坏对错",
                "template": "你是一位忠实记录员。你的唯一职责是把视频里实际发生和说出的一切，按原有顺序清楚地记下来，像做会议记录那样准确、不加修饰。你不评判讲得好不好、说得对不对、值不值得听，也不补充视频之外的任何知识、资料或看法——凡是视频里没有出现过的内容，你一概不写。你要做的是把「他说了什么、为什么这么说」如实摊开，让没看过视频的人照着能复述出来。",
            },
            {
                "key": "academic",
                "name": "学术研究员",
                "desc": "严谨论证，挖掘思想脉络",
                "template": "你是一位受过系统学术训练的研究员，擅长人文社科领域的文本分析。你注重论证结构的梳理、核心概念的辨析、以及观点之间逻辑关系的把握。你会标注信息的来源层级（原文明确陈述 / 合理推断 / 缺乏依据），但不会过度学术化。",
            },
            {
                "key": "tech",
                "name": "技术审稿人",
                "desc": "注重可操作性和技术细节",
                "template": "你是一位技术审稿人，擅长从实操角度审视内容。你关注步骤的可执行性、技术细节的准确性、以及潜在的操作风险。你倾向于把抽象描述转化为具体步骤，并指出哪些是必须的、哪些是可选的。",
            },
            {
                "key": "journalist",
                "name": "新闻编辑",
                "desc": "事实优先，区分事实与观点",
                "template": "你是一位新闻编辑，习惯用 5W1H 框架快速提取核心事实。你严格区分事实陈述、推测、评论和宣传话术，对信息来源的可靠性敏感。你的表达简洁客观，避免主观渲染。",
            },
            {
                "key": "creative",
                "name": "创意写手",
                "desc": "生动有趣，善于捕捉亮点",
                "template": "你是一位创意写手，善于捕捉视频中的精彩瞬间和有趣细节。你的表达生动有画面感，能还原对话的氛围和情绪。你会在保证信息准确的前提下，让总结读起来有吸引力。",
            },
            {
                "key": "tcm",
                "name": "中医学术顾问",
                "desc": "中医内容整理与医理辨析：术语引文原貌保留，引语人物补注不外加",
                "template": "你是一位熟悉中医基础理论、中药方剂、经典文献与各家学说的学术顾问，长期整理并辨析中医讲座与科普视频。整理时严格保留专业术语原貌：穴位、经络、方剂、中药、证型等不做同义替换或现代化改写；经典条文与方歌逐字引用，不把异体字规范化（如“恬惔”不改“恬淡”）；剂量与单位（克、钱、两、贴、剂）原样保留，不换算。你不止于整理——把视频中实际出现的各家说法的异同、源流、依据与适用边界辨析清楚；视频中提到某医家、经典或引语但语焉不详时，补全出处与原句、补足被提及者的背景，补注一律标明并与讲者原话严格分离，视频未提及的内容不得引入。你的辨析意见以「辨析者按」单独标注，不混入讲者观点。",
            },
        ],
    },

    # ────────────── 2. 认知规则 ──────────────
    "rules": {
        "label": "认知规则",
        "desc": "分析的深度和原则，决定 AI 如何思考内容",
        "default": "standard",
        "modules": [
            {
                "key": "standard",
                "name": "标准提炼",
                "desc": "忠实提取，不发挥",
                "template": """【分析原则】
1. 忠实于原文内容，不添加视频中未提及的信息
2. 区分核心论点和支撑细节，优先呈现核心
3. 保留说话者的原始用词和立场，不替它做价值判断
4. 如遇模糊或矛盾之处，如实标注，不强作解人""",
            },
            {
                "key": "descriptive",
                "name": "只描述·不评述",
                "desc": "锁定视频内部：可还原理由与暗示，不评好坏对错",
                "template": """【分析原则】
1. 只写视频里实际出现的内容：讲了什么、做了什么、按什么顺序推进。视频里没有的，不写。
2. 不做评价：不评质量高低、不辨对错是非、不论价值大小、不评讲得好不好；不用「精彩」「重要」「深刻」「不足」「值得注意」「令人深思」这类带褒贬色彩的词。
3. 不引外部标尺：不补充视频之外的知识、背景、资料，也不用外部标准去衡量视频里的内容。注意这不等于不许讲理由——讲者自己给出的依据必须讲清楚。
4. 讲清楚「为什么这么说」：还原讲者提出某个说法时所依据的理由、根据、前提，以及他前后话语之间的推进关系（因为……所以……／先摆出问题再给方案／先用例子再收成结论）。原文明说理由的照录；视频没明说、但能从上下文推出的，就按「视频里的依据是……」写出来，并标明这是从原文推出来的、不是讲者的原话。
5. 可以点出话里的暗示与未明说的前提：讲者话里带出的言外之意、他默认成立却没展开解释的假设——只要能指回视频中的具体某一句或某一段，就可以写；指不回去的不要写，宁可不提。
6. 允许的加工只有这三项——整理（归类、去重、理清前后关系与脉络）、简化（删口头禅、过渡语和无信息量的重复，保留必要的限定条件）、以及上面第 4、5 条的分析。三者都以不改变原意为界。
7. 简化只删不减义：不得改变原意、不得省略限定条件、不得把不确定的说成确定的；关键的原话、术语、专名按视频原样保留。
8. 如实标注，不强作解人：听不清、语境缺失、自相矛盾的地方原样标出（如 [存疑]、[听不清]），既不替讲者圆场，也不掩盖矛盾——但这不禁止按第 4、5 条做基于原文的推断。""",
            },
            {
                "key": "deep",
                "name": "深度分析",
                "desc": "梳理论证结构，挖掘隐含逻辑",
                "template": """【分析原则】
1. 不仅提取信息，还要梳理论证结构：前提→推理→结论
2. 标注信息的确定性层级：[原文] 明确陈述 / [推断] 合理推测 / [存疑] 证据不足
3. 识别隐含前提和未明说的假设
4. 注意概念在不同语境中的含义差异
5. 保留复杂性和张力，不做过度简化""",
            },
            {
                "key": "critical",
                "name": "批判审视",
                "desc": "评估论据质量，指出强弱",
                "template": """【分析原则】
1. 区分"论证"与"断言"——有证据支撑的才是论证
2. 评估每个论据的说服力：证据是否充分、逻辑是否自洽
3. 指出内容的亮点和不足，给出具体理由
4. 识别可能的偏见、逻辑谬误或信息选择性
5. 对可以改进或需补充的方面给出建议
6. 保持中立，不因个人立场而褒贬""",
            },
        ],
    },

    # ────────────── 3. 输出格式 ──────────────
    "output": {
        "label": "输出格式",
        "desc": "总结的结构和呈现方式",
        "default": "summary",
        "modules": [
            {
                "key": "summary",
                "name": "综合摘要",
                "desc": "结构化摘要，含核心主题和要点",
                "template": """【输出格式】
按以下结构输出（各部分按需调整，不要硬凑）：

## 核心主题
用 1-2 句话概括视频的核心内容。

## 主要内容
分条梳理视频的关键信息点，每条一个完整观点，保持逻辑顺序。重要处标注时间戳 [MM:SS]。

## 关键细节
补充重要的数据、案例、引用或操作细节。

## 一句话总结
用一句话收束。""",},
            {
                "key": "bullets",
                "name": "要点列表",
                "desc": "精炼的要点清单",
                "template": """【输出格式】
以要点列表为主，每条不超过 2 句话：
- 第一条：视频讲的是什么（1 句话）
- 后续条目：按重要性或时间顺序列出关键要点
- 最后一条：值得记住的一句话

要求精炼，不要展开论述。关键处可标注 [MM:SS]。""",},
            {
                "key": "qa",
                "name": "问答对",
                "desc": "以问答形式组织内容",
                "template": """【输出格式】
以问答形式呈现，提出 6-10 个问题并作答：
- 问题应覆盖视频的核心内容，而非细枝末节
- 答案基于视频内容，简洁准确
- 格式：**Q：问题** \\n A：答案

不要编造视频中未涉及的问题。""",},
            {
                "key": "mindmap",
                "name": "思维导图",
                "desc": "Markdown 树状结构",
                "template": """【输出格式】
用 Markdown 缩进层级输出思维导图结构：
- 根节点是视频核心主题
- 一级分支是主要内容板块
- 二级及以下是具体要点
- 每个节点简洁，不超过 1 句话

示例：
# 核心主题
## 分支1
- 要点1
- 要点2
  - 细节
## 分支2
- 要点""",},
            {
                "key": "timeline",
                "name": "时间线",
                "desc": "按时间/逻辑顺序串联",
                "template": """【输出格式】
按视频的展开顺序（时间线或逻辑线）梳理：

## 内容走向
1-2 句话说明视频的整体脉络。

## 时间线
| 时间 | 内容要点 |
|------|----------|
| [MM:SS] | ... |
| [MM:SS] | ... |

## 关键转折
标出视频中重要的转折点或观点切换。""",},
            {
                "key": "chapters",
                "name": "章节分析",
                "desc": "按内容板块逐段分析",
                "template": """【输出格式】
按内容的逻辑板块分章节（不是机械的时间分段，而是按话题/论点切换）：

## 整体综述
先给出一段 300-600 字的整体综述，连贯成文、不分点：视频围绕什么主题展开，核心主张或主线是什么，按什么线索分几部分推进，各部分之间如何衔接（铺垫、递进、转折、呼应），整体上落到什么结论或留下什么待解的问题。这是全篇纲领，读者只读这一段也应能把握整体脉络，因此要写透写完整；但只做全局概括，不展开章节细目，不堆砌罗列。

### 第一章 [MM:SS-MM:SS] 章节标题
- 本章要点
- 核心观点

### 第二章 ...
（依此类推）

## 整体评价
最后对视频内容本身做一段 200-400 字的整体评价——评价的是内容实质，不是章节结构或讲述形式：
- 核心主张是否站得住：讲者拿出的依据是什么类型（经典依据、临床经验、推理演绎、个案举例），支撑是否充分、推理是否成立
- 哪些内容讲得透彻、有见地或信息价值高
- 讲者说得含糊、容易误读、或前提没交代的关键处，以视频原文为主、学科通识为辅，帮着把话说透，标注[补注]并与讲者原意严格区分；本来就讲清楚的不重复评，不为评而评
- 哪些内容依据不足、以偏概全、前后矛盾或明显存疑
- 围绕该主题是否有应讲而未讲的关键环节
每一条评价必须有理有据，落到具体章节或时间戳；评价代表整理者立场，与讲者观点明确区分，不因讲者权威而回避问题。没有实质可评的就如实简短带过，不硬凑一节。""",},
            {
                "key": "process_ledger",
                "name": "过程盘点表",
                "desc": "按发生顺序盘点，讲了什么/为什么这么说/推进到哪，不设评价栏",
                "template": """【输出格式】
只输出以下三块，不设评价、感想、优点、不足、改进建议等任何主观栏目：

## 一、这段视频里发生了什么
3-5 句连贯叙述，只写事实：讲者是谁、讲的是什么、这件事从什么状态进行到什么状态。不做评价。

## 二、过程盘点
按视频展开的先后顺序分节，每节写清时段和内容：

### 环节 N：[MM:SS]-[MM:SS] <用一句话写这一环节在做什么>
- **讲了什么**：本段的内容要点，已去重、已简化，保留必要的限定条件
- **依据与为什么这么说**：本段说法的理由、根据、前提，以及它与前后环节的推进关系；原文明说的照录，从上下文推出的标明是推断
- **用到的材料**：本段提到的例子、数据、步骤、引用（如有；没有就不写这一条）
- **产出/变化**：本段结束时得出的结果、结论，或事情推进到了哪一步（只写讲者说出来的结果）

## 三、未能确定的地方
逐条列出听不清、语境缺失、自相矛盾、视频未交代清楚的部分，说明缺的是什么。没有就写「无」。""",},
            {
                "key": "comparison",
                "name": "对比表格",
                "desc": "以表格对比不同观点/方案",
                "template": """【输出格式】
如果视频涉及多个观点、方案或对象的对比，用表格呈现：

## 对比维度
| 维度 | 观点A | 观点B | ... |
|------|-------|-------|-----|
| ... | ... | ... | ... |

## 补充说明
表格之外的背景信息和重要细节。

如果视频内容不适合表格对比，改为分类要点列表。""",},
            {
                "key": "freeform",
                "name": "自由格式",
                "desc": "AI 根据内容自行决定结构",
                "template": """【输出格式】
根据视频内容的性质，自行选择最合适的呈现结构。不拘泥于固定模板，以让读者高效获取信息为原则。可以用标题、列表、表格等 Markdown 元素。""",},
        ],
    },

    # ────────────── 4. 内容类型 ──────────────
    "content_type": {
        "label": "内容类型",
        "desc": "针对不同类型视频的理解方式",
        "default": "general",
        "modules": [
            {
                "key": "general",
                "name": "通用视频",
                "desc": "不做类型预设",
                "template": "",  # 空模板，不添加额外约束
            },
            {
                "key": "tutorial",
                "name": "教程/操作",
                "desc": "提取可执行步骤",
                "template": """【内容理解】
这是一个教程或操作演示类视频。请特别关注：
- 最终目标是什么
- 需要哪些工具、环境或前置条件
- 具体操作步骤（按顺序，区分必须和可选）
- 常见问题和注意事项
目标：让读者不看视频也能独立完成操作。""",
            },
            {
                "key": "news",
                "name": "新闻资讯",
                "desc": "5W1H 分离事实与观点",
                "template": """【内容理解】
这是新闻或资讯类内容。请特别关注：
- 5W1H：谁、什么事、何时、何地、为什么、如何
- 区分已确认事实、相关方声称、分析推测、评论观点
- 注意信息来源的可靠性
- 标注尚未证实或存在争议的部分""",
            },
            {
                "key": "interview",
                "name": "访谈/对话",
                "desc": "区分不同发言者的立场",
                "template": """【内容理解】
这是访谈或对话类内容。请特别关注：
- 区分不同发言者及其立场
- 提炼每位发言者的核心观点
- 标注观点之间的交锋、共识或分歧
- 保留有价值的原话引用
- 不要把不同人的观点混为一谈""",
            },
            {
                "key": "lecture",
                "name": "演讲/讲座",
                "desc": "梳理论证结构和核心概念",
                "template": """【内容理解】
这是演讲或讲座类内容。请特别关注：
- 演讲者的核心论点是什么
- 论证结构：用了哪些论据、案例、推理
- 关键概念的定义和用法
- 演讲者立场和可能的预设
- 现场互动中有价值的问题""",
            },
            {
                "key": "entertainment",
                "name": "休闲/娱乐",
                "desc": "捕捉亮点和氛围",
                "template": """【内容理解】
这是休闲或娱乐类内容。请特别关注：
- 视频的亮点和精彩片段（标注时间戳）
- 传达的核心信息或情绪
- 有笑点、有梗、有名场面的地方
- 是否有值得了解的背景知识
不必强行提炼深层意义，轻松内容轻松总结。""",
            },
            {
                "key": "academic_study",
                "name": "学术研究整理",
                "desc": "侧重学术整理：概念、文献、谱系",
                "template": """【内容理解】
这是学术研究或理论讲授类内容。请特别侧重学术整理，关注：
- 核心概念的精确定义、来源与边界（自定义/引用/约定）
- 理论的脉络：提出了什么问题、回应了什么争论、修正了什么观点
- 学术谱系定位：与哪些学派/思想家/文本存在继承、批评或对话关系
- 论证链条：从问题意识→核心命题→论据→结论的完整路径
- 证据与材料：引用的文献、数据、案例、思想实验
- 研究的边界与局限：作者承认了什么、回避了什么
目标：产出一份可作为学术笔记/读书报告直接使用的整理稿。""",
            },
            {
                "key": "argument",
                "name": "论述观点整理",
                "desc": "专注论述整理：论点、论据、论证",
                "template": """【内容理解】
这是论述或观点表达类内容。请专注论述整理，关注：
- 核心论点（作者试图主张什么）与分论点
- 论据类型：事实数据、权威引用、逻辑推理、类比、案例
- 论证结构：归纳/演绎/类比，前提与结论如何衔接
- 对反驳的处理：作者如何回应可能的质疑
- 立场与预设：作者的立场、前提假设、可能的偏见
- 概念的滑移或偷换（如有）
目标：把"说了什么、凭什么这么说、说得成不成立"梳理清楚。""",
            },
            {
                "key": "event",
                "name": "事件叙事整理",
                "desc": "侧重事件整理：经过、因果、影响",
                "template": """【内容理解】
这是围绕特定事件/经历展开的内容。请侧重事件整理，关注：
- 事件要素：时间、地点、人物、起因
- 发展经过：按时间/因果顺序还原完整链条
- 关键节点与转折：什么改变了走向
- 各方角色：谁做了什么、动机是什么
- 结果与影响：事件的直接后果、后续发展、各方反应
- 叙事中的主观视角：讲述者立场对事件的取舍
目标：让读者不看视频也能完整复述事件的来龙去脉。""",
            },
            {
                "key": "storytelling",
                "name": "故事/纪实",
                "desc": "还原叙事结构与人物弧光",
                "template": """【内容理解】
这是故事讲述或纪实类内容。请特别关注：
- 叙事结构：起承转合、冲突的建立与解决
- 人物：角色关系、动机、性格弧光（如何变化）
- 场景与氛围：关键场景的细节和情绪
- 主题：故事在讲什么更深层的东西
- 讲述技巧：悬念、伏笔、呼应
目标：还原故事的骨架，让人理解它为什么打动人。""",
            },
        ],
    },

    # ────────────── 5. 深度 ──────────────
    "depth": {
        "label": "深度",
        "desc": "分析的细粒度，影响篇幅和详略",
        "default": "standard",
        "modules": [
            {
                "key": "quick",
                "name": "快速浏览",
                "desc": "500字以内，只抓核心",
                "template": """【深度要求】
快速浏览模式：只提取最核心的信息，帮助用户在 30 秒内决定是否值得深入看。
- 聚焦"视频讲什么"和"值不值得看"
- 不展开细节和论证
- 总字数控制在 500 字以内""",
            },
            {
                "key": "standard",
                "name": "标准总结",
                "desc": "3000字左右，平衡详略",
                "template": """【深度要求】
标准总结模式：在信息完整性和简洁性之间取得平衡。
- 覆盖视频的所有主要要点，但不逐句复述
- 重要细节保留，次要细节省略
- 篇幅适中，不堆砌也不遗漏""",
            },
            {
                "key": "deep",
                "name": "深度拆解",
                "desc": "10000字+，详尽分析",
                "template": """【深度要求】
深度拆解模式：尽可能详尽地分析内容。
- 不遗漏任何有信息量的观点、论据、案例
- 对关键段落可以做较详细的展开
- 标注时间戳方便回看原文
- 适合需要反复研读的场景""",
            },
            {
                "key": "research",
                "name": "深度研究",
                "desc": "提取主题→逐主题深度扩展→研究博文",
                "template": """【深度要求】
深度研究模式：基于视频内容做主题式深度扩展，输出结构化研究博文。
- 从内容中提取 3-8 个值得展开的研究主题
- 每个主题 800-1500 字深度分析，围绕视频给出的论据、案例展开
- 包含背景脉络、不同观点、延伸思考
- 扩展论述只能基于视频内容本身，不得虚构外部资料、数据来源或“搜索结果”
- 适合需要深度学习的学术场景""",
            },
        ],
    },

    # ────────────── 6. 语气（复用 prompts.py 的 TONE_DESCS） ──────────────
    "tone": {
        "label": "语气",
        "desc": "总结的表达风格",
        "default": "学术严谨",
        "modules": [
            {"key": "学术严谨", "name": "学术严谨", "desc": "术语精确、论证严密"},
            {"key": "通俗科普", "name": "通俗科普", "desc": "用日常比喻帮助理解"},
            {"key": "口语化", "name": "口语化", "desc": "自然口语，像跟同学讨论"},
            {"key": "新闻客观", "name": "新闻客观", "desc": "事实优先、立场标注"},
            {"key": "正式商务", "name": "正式商务", "desc": "结构清晰、结论明确"},
        ],
    },

    # ────────────── 7. 字数（复用 prompts.py 的 WORD_COUNT_DESCS） ──────────────
    "word_count": {
        "label": "字数",
        "desc": "总结的字数控制",
        "default": "3000",
        "modules": [
            {"key": "不限", "name": "不限", "desc": "按内容需要自由发挥"},
            {"key": "500", "name": "500字", "desc": "极简"},
            {"key": "3000", "name": "3000字", "desc": "标准"},
            {"key": "5000", "name": "5000字", "desc": "详细"},
            {"key": "10000", "name": "10000字", "desc": "深度"},
            {"key": "20000", "name": "20000字", "desc": "极详尽"},
        ],
    },

    # ────────────── 8. 输出语言（复用 prompts.py 的 LANGUAGE_DESCS；默认中文，外文素材翻译呈现） ──────────────
    "language": {
        "label": "输出语言",
        "desc": "总结的输出语言",
        "default": "简体中文",
        "modules": [
            {"key": "简体中文", "name": "简体中文", "desc": "外文素材翻译成中文呈现"},
            {"key": "繁体中文", "name": "繁体中文", "desc": "繁体中文输出"},
            {"key": "英语", "name": "英语", "desc": "英文输出"},
            {"key": "跟随素材", "name": "跟随素材", "desc": "素材什么语言就输出什么语言"},
        ],
    },

    # ────────────── 8. 模式（通用 / 社科深度特化） ──────────────
    # 控制整体认知框架：通用=精简忠实提取；社科深度特化=注入旧15种提示词的精华
    "mode": {
        "label": "认知模式",
        "desc": "整体认知框架（通用精简 / 社科深度特化 / 中医特化）",
        "default": "general",
        "modules": [
            {
                "key": "general",
                "name": "通用",
                "desc": "精简忠实提取，适合日常视频",
                "template": "",  # 空模板，不添加额外认知约束
            },
            {
                "key": "social_science",
                "name": "社科深度特化",
                "desc": "四层认知+置信度标注+思想史定位，适合学术内容",
                "template": """【社科深度特化·认知框架】
1. 四层认知标准：事实准确复述 → 深层结构揭示 → 理论坐标系定位 → 临界自反思辨
2. 置信度标注：所有论断须标注 [原文]可定位时间戳 / [推断]附推导逻辑 / [假设]声明置信度 / [真空]明确告知无相关信息
3. 概念精确性：关键概念须给出精确学术定义，标注定义来源（自定义/引用/约定）；同一概念的多次不同表述需分别记录，不得合并
4. 思想史定位：在适用处标注内容与思想传统/学术谱系的关系（继承/批评/修正/并列），并区分作者明确表达 vs 基于文本的合理推断
5. 描述 vs 规范：严格区分描述性内容与规范性内容；保留作者的方法论自觉表述
6. 辩证保留：保留论证的辩证推进关系，不得将正反环节简化为并列分支""",
            },
            {
                "key": "tcm",
                "name": "中医特化",
                "desc": "以中医理论体系为认知坐标：引文分级+辨证链完整，适合中医讲座与经典解读",
                "template": """【中医特化·认知框架】
1. 盘点为基：只盘点视频实际讲到的内容，把不同医理观点各自说清楚；不引入视频未提及的学说、医家或资料
2. 术语坐标系：以中医自身理论体系（阴阳五行、脏腑经络、气血津液、病因病机、证型方剂）理解与组织内容，不用现代医学框架强行转译；中西医对照仅记录讲者明确说出的部分
3. 语境辨析：同一术语在不同经典与流派中含义不同（如“伤寒”在《伤寒论》与温病学派），分别记录，不合并、不跨语境混用
4. 引文分级：[经典原文]/[医家原说]/[讲者观点]/[补注]/[辨析者按] 分开标注，各归其位，互不渗入；经典条文逐字保留，异体字不规范化（“恬惔”不改“恬淡”）
5. 辨证链完整：讲“理-法-方-药”时保留完整推理链（证→治法→方剂→加减），君臣佐使与剂量单位（克、钱、两、贴、剂）原貌保留，不换算
6. 辨析与补注：对视频中实际出现的不同说法做四步辨析——溯源（出自何经典/何医家/何流派，以视频提及为准）→ 明据（视频给出的论据：经文/医案/临床经验/推理）→ 辨异（分歧点在概念定义/理论前提/适用范围）→ 判适用（视频语境下什么条件下成立）；视频提到某医家、经典条文或引语但只说一半时，补全出处与原句，提到对某人的评价时补足被评价者的必要背景——仅限视频提及的对象，补不准宁可不补，不臆造出处与原句。整体评价一节可引入学科通识把讲者没说清楚的关键处说透：以视频原文为主、通识为辅，标注[补注]，不做学说站队
7. 结论分层不下定论：共识（视频内各家一致）/ 条件性成立（各家其是，适用条件不同）/ 存疑待考（视频依据不足，标 [存疑]）；只分层，不替用户裁决谁家对
8. 存疑存真：听不清、写不准的术语与条文如实标注[存疑]，保留原貌，不按“最可能”臆改""",
            },
        ],
    },

    # ────────────── 10. 口误与转写纠错（单模块维度：文本保真纪律，默认启用） ──────────────
    # 背景：转录（含 AI 校正后）仍会残留同音/近音误写与讲者口误，总结逐字复述时会把错误带进正文。
    # 纪律只作用于总结自己的转述文字；直接引语/经典条文逐字保留的既有纪律（如 tcm 引文分级）不受影响。
    # 用户要求（2026-10-05）：纠错判定 = 学科术语体系 + 上下文语境 两者结合，缺一不改。
    "speech_fix": {
        "label": "口误与转写纠错",
        "desc": "对转录中同音/近音误写与明显口误的处理纪律",
        "default": "balanced",
        "modules": [
            {
                "key": "balanced",
                "name": "按学科与上下文纠错",
                "desc": "学科术语体系与上下文语境共同判定，拿不准保留原样",
                "template": """【转录纠错纪律】
转录文本可能含有同音/近音误写（如把“饮片”误写成“影片”）与讲者口误。整理转述时：
- 先判断内容的学科领域，纠错以「学科术语体系 + 上下文语境」共同判定：术语还原必须
  符合该学科的通行写法，且与上下文语义一致；不按日常用语习惯随意“通顺化”，
  不用另一学科的说法替换；
- 学科知识与上下文能共同明确判定原意的明显错字，按该学科的正确写法呈现，
  不把错误原字复制进总结；两者缺一（学科上有把握但上下文不支持，或反之）则不轻改；
- 把握不足的一律保留原文字样，禁止凭猜测改写，禁止引入原文没有的内容，不得整句重写；
- 直接引用的原话与经典条文仍逐字保留，本纪律只作用于你自己的转述文字。""",
            },
        ],
    },
}


# ============ 预设配方（一键填充所有维度；统一钉 language=简体中文，应用即回默认简中） ============
# 精简原则：维度模块（内容类型/输出格式等）保持全量可选，配方只保留高频组合；
# 被裁组合的内容类型（学术整理/论述/事件/故事/娱乐/对比）仍可在维度选择器手动搭配。
RECIPES = [
    {
        "key": "quick_watch",
        "name": "📺 视频速看",
        "desc": "快速判断视频值不值得看",
        "dimensions": {
            "role": "general",
            "mode": "general",
            "rules": "standard",
            "output": "bullets",
            "content_type": "general",
            "depth": "quick",
            "tone": "口语化",
            "word_count": "500",
            "language": "简体中文",
            "speech_fix": "balanced",
        },
    },
    {
        "key": "process_inventory",
        "name": "📋 过程盘点",
        "desc": "只盘点视频内发生的过程：整理、简化、讲清为什么这么说，不做任何评价",
        "dimensions": {
            "role": "recorder",
            "mode": "general",
            "rules": "descriptive",
            "output": "process_ledger",
            "content_type": "general",
            "depth": "standard",
            "tone": "新闻客观",
            "word_count": "3000",
            "language": "简体中文",
            "speech_fix": "balanced",
        },
    },
    {
        "key": "news_brief",
        "name": "📰 新闻要点",
        "desc": "快速获取新闻核心事实",
        "dimensions": {
            "role": "journalist",
            "mode": "general",
            "rules": "standard",
            "output": "summary",
            "content_type": "news",
            "depth": "standard",
            "tone": "新闻客观",
            "word_count": "3000",
            "language": "简体中文",
            "speech_fix": "balanced",
        },
    },
    {
        "key": "tutorial_steps",
        "name": "🔧 教程步骤",
        "desc": "提取可执行的操作步骤",
        "dimensions": {
            "role": "tech",
            "mode": "general",
            "rules": "standard",
            "output": "bullets",
            "content_type": "tutorial",
            "depth": "standard",
            "tone": "通俗科普",
            "word_count": "3000",
            "language": "简体中文",
            "speech_fix": "balanced",
        },
    },
    {
        "key": "talk_digest",
        "name": "🎙️ 对话整理",
        "desc": "访谈对话的观点整理",
        "dimensions": {
            "role": "general",
            "mode": "general",
            "rules": "standard",
            "output": "qa",
            "content_type": "interview",
            "depth": "standard",
            "tone": "口语化",
            "word_count": "3000",
            "language": "简体中文",
            "speech_fix": "balanced",
        },
    },
    {
        "key": "course_notes",
        "name": "📝 课程笔记",
        "desc": "学术讲座的深度学习笔记",
        "dimensions": {
            "role": "academic",
            "mode": "social_science",
            "rules": "deep",
            "output": "chapters",
            "content_type": "lecture",
            "depth": "deep",
            "tone": "学术严谨",
            "word_count": "10000",
            "language": "简体中文",
            "speech_fix": "balanced",
        },
    },
    {
        "key": "deep_research",
        "name": "🧠 深度研究",
        "desc": "批判性深度分析",
        "dimensions": {
            "role": "academic",
            "mode": "social_science",
            "rules": "critical",
            "output": "summary",
            "content_type": "general",
            "depth": "deep",
            "tone": "学术严谨",
            "word_count": "10000",
            "language": "简体中文",
            "speech_fix": "balanced",
        },
    },
    {
        "key": "tcm_lecture",
        "name": "🌿 中医讲堂",
        "desc": "中医讲座/科普深度整理与医理辨析：术语、方剂、经典引文原貌保留，引语人物补注",
        "dimensions": {
            "role": "tcm",
            "mode": "tcm",
            "rules": "deep",
            "output": "chapters",
            "content_type": "lecture",
            "depth": "deep",
            "tone": "学术严谨",
            "word_count": "10000",
            "language": "简体中文",
            "speech_fix": "balanced",
        },
    },
]


# ============ 维度默认值（用于初始化） ============
def get_default_dimensions() -> Dict[str, str]:
    """返回所有维度的默认选择"""
    return {dim_key: dim_cfg["default"] for dim_key, dim_cfg in PROMPT_DIMENSIONS.items()}


# ============ DB 覆盖层（prompt_module_overrides 表） ============
def _load_override_rows() -> List[Dict]:
    """
    从数据库加载覆盖原始行（含 display_name 等元数据）
    如果 DB 不可用或表不存在，返回空 list（降级到代码默认）
    """
    try:
        from db.database import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT dimension_key, module_key, custom_text, display_name "
            "FROM prompt_module_overrides"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        # 表不存在或 DB 异常：降级到代码默认
        return []


def _load_module_overrides() -> Dict[str, Dict[str, str]]:
    """
    从数据库加载模块文本覆盖（dimension_key → {module_key → custom_text}）
    如果 DB 不可用或表不存在，返回空 dict（降级到代码默认）
    """
    overrides: Dict[str, Dict[str, str]] = {}
    for r in _load_override_rows():
        dim = r["dimension_key"]
        mod = r["module_key"]
        overrides.setdefault(dim, {})[mod] = r["custom_text"] or ""
    return overrides


def _get_module_template(dim_key: str, module_key: str,
                         overrides: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    """
    获取某个维度下某个模块的 template 文本
    优先级：DB 覆盖 > 代码默认（自定义模块只存在于覆盖层，天然走第 1 优先级）
    """
    # 1. 先查 DB 覆盖
    if overrides and dim_key in overrides and module_key in overrides[dim_key]:
        return overrides[dim_key][module_key]
    # 2. 回退到代码默认
    dim = PROMPT_DIMENSIONS.get(dim_key, {})
    for m in dim.get("modules", []):
        if m["key"] == module_key:
            return m.get("template", "")
    return ""


def get_dimensions_with_overrides() -> dict:
    """
    返回带 DB 覆盖文本的完整维度定义（供前端 /dimensions API）
    标注每个模块是否被自定义覆盖；内置模块之后的自定义模块
    （module_key 不在代码内置列表，模板中心「新增自定义模块」产生）追加到维度末尾
    """
    rows = _load_override_rows()
    overrides: Dict[str, Dict[str, str]] = {}
    display_names: Dict[tuple, str] = {}
    for r in rows:
        dim, mod = r["dimension_key"], r["module_key"]
        overrides.setdefault(dim, {})[mod] = r["custom_text"] or ""
        display_names[(dim, mod)] = r["display_name"] or ""
    result = {}
    for dim_key, dim_cfg in PROMPT_DIMENSIONS.items():
        dim_copy = {
            "label": dim_cfg["label"],
            "desc": dim_cfg["desc"],
            "default": dim_cfg["default"],
            "modules": [],
        }
        dim_overrides = overrides.get(dim_key, {})
        for m in dim_cfg["modules"]:
            m_copy = dict(m)
            if m["key"] in dim_overrides:
                m_copy["template"] = dim_overrides[m["key"]]
                m_copy["is_customized"] = True
            else:
                m_copy["is_customized"] = False
            dim_copy["modules"].append(m_copy)
        # 自定义模块：覆盖表里有、代码内置列表没有的 module_key
        builtin_keys = {m["key"] for m in dim_cfg["modules"]}
        for mod_key, text in dim_overrides.items():
            if mod_key in builtin_keys:
                continue
            dim_copy["modules"].append({
                "key": mod_key,
                "name": display_names.get((dim_key, mod_key)) or mod_key,
                "desc": "自定义模块（模板中心新增）",
                "template": text,
                "is_customized": True,
                "is_custom": True,
            })
        result[dim_key] = dim_copy
    return result


# ============ 语气和字数描述（复用 prompts.py 的映射） ============
def _get_tone_desc(tone: str) -> str:
    from services.prompts import TONE_DESCS
    return TONE_DESCS.get(tone, f"使用{tone}语气")


def _get_word_count_desc(word_count: str) -> str:
    from services.prompts import WORD_COUNT_DESCS
    return WORD_COUNT_DESCS.get(word_count, f"请务必输出至少{word_count}字，宁多勿少，绝对不允许少于{word_count}字。")


def _get_language_desc(language: str) -> str:
    from services.prompts import LANGUAGE_DESCS
    return LANGUAGE_DESCS.get(language, "")


# ============ 核心：拼装完整 prompt ============
def build_modular_prompt(dimensions: Dict[str, str], content: str,
                         custom_prompt: str = "",
                         full_override: str = "") -> str:
    """
    根据 10 维选择拼装完整 prompt
    dimensions: {role, rules, output, content_type, depth, tone, word_count, mode, language, speech_fix}
    content: 待总结的转录文本
    custom_prompt: 用户维度级自定义指令（作为额外指引追加）
    full_override: 完全自定义提示词（非空时直接覆盖一切维度拼装，仅注入 content）
    """
    # ★ 完全自定义模式：覆盖一切维度拼装
    if full_override and full_override.strip():
        prompt = full_override.strip()
        # 仅做内容注入（支持 {{CONTENT}} 占位符或直接追加）
        if "{{CONTENT}}" in prompt:
            return prompt.replace("{{CONTENT}}", content or "")
        return prompt + "\n\n【待处理内容】\n" + (content or "")

    # 合并默认值，防止缺维度
    dims = get_default_dimensions()
    if dimensions:
        dims.update(dimensions)

    # 加载 DB 覆盖（一次性加载，避免多次查询）
    overrides = _load_module_overrides()

    # 收集各维度模块文本（跳过空模板）
    sections = []

    # 1. 角色
    role_tpl = _get_module_template("role", dims["role"], overrides)
    if role_tpl:
        sections.append(role_tpl)

    # 2. 认知模式（社科深度特化注入四层认知+置信度标注）
    mode_tpl = _get_module_template("mode", dims["mode"], overrides)
    if mode_tpl:
        sections.append(mode_tpl)

    # 3. 内容类型
    ct_tpl = _get_module_template("content_type", dims["content_type"], overrides)
    if ct_tpl:
        sections.append(ct_tpl)

    # 4. 认知规则
    rules_tpl = _get_module_template("rules", dims["rules"], overrides)
    if rules_tpl:
        sections.append(rules_tpl)

    # 4.5 转录纠错纪律（单模块维度；模板为空或值未知时不追加）
    sf_tpl = _get_module_template("speech_fix", dims.get("speech_fix", ""), overrides)
    if sf_tpl:
        sections.append(sf_tpl)

    # 5. 深度
    depth_tpl = _get_module_template("depth", dims["depth"], overrides)
    if depth_tpl:
        sections.append(depth_tpl)

    # 6. 输出格式
    output_tpl = _get_module_template("output", dims["output"], overrides)
    if output_tpl:
        sections.append(output_tpl)

    # 7. 维度级自定义指令（如果有）
    if custom_prompt and custom_prompt.strip():
        sections.append(f"【自定义指引】\n{custom_prompt.strip()}")

    # 8. 语气 + 字数 + 输出语言
    tone_desc = _get_tone_desc(dims["tone"])
    word_count_desc = _get_word_count_desc(dims["word_count"])
    lang_desc = _get_language_desc(dims.get("language", ""))
    option_block = ""
    if tone_desc:
        option_block += f"语气：{tone_desc}。\n"
    if word_count_desc:
        option_block += word_count_desc
    # 输出语言固定拼在字数之后（产品默认中文；选"跟随素材"时也明确写出）
    if lang_desc:
        option_block += f"\n输出语言：{lang_desc}。"
    if option_block:
        sections.append(option_block.strip())

    # 8. 内容（直接注入到 prompt 中）
    if content:
        sections.append(f"【待处理内容】\n{content}")
    else:
        sections.append("【待处理内容】\n{{CONTENT}}")

    return "\n\n---\n\n".join(sections)


def dimensions_to_legacy_summary_type(dimensions: Dict[str, str]) -> str:
    """
    将 dimensions 映射回旧的 summary_type（用于数据库兼容和日志）
    仅用于日志显示，不影响实际 prompt 构建
    """
    if not dimensions:
        return "modular"
    output = dimensions.get("output", "summary")
    depth = dimensions.get("depth", "standard")
    # 简单映射：快速浏览+要点 → quick, 深度+章节 → chapter, 等
    if depth == "quick":
        return "quick"
    if output == "chapters":
        return "chapter"
    if output == "qa":
        return "qa"
    if output == "mindmap":
        return "mindmap"
    if output == "timeline":
        return "timeline"
    if output == "comparison":
        return "comparison"
    return "summary"
