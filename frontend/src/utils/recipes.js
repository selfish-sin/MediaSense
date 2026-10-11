/**
 * 维度配方工具（SplitPromptPanel 与 DimensionSelector 共用）
 */

// 维度列表（顺序即 UI 展示顺序，与后端 prompt_builder.DIMENSION_ORDER 一一对应，改动必须两边同步）
// 顺序逻辑：人设与内容类型 → 认知框架与立场 → 分析粒度 → 输出形态 → 语气 → 字数 → 纠错纪律
// 2026-10-11 维度精简：删「输出语言」（固定简体中文，纪律硬编码进 prompt），10 → 9 维；
// 「认知规则」改名「分析立场」、「输出格式」改名「输出形态」（维度 key 不变，历史任务数据兼容）。
// matchRecipeKey 只比较配方定义过的维度——配方未定义的维度不影响高亮，新增维度不会让已有配方失效
export const DIMENSION_KEYS = ['role', 'content_type', 'mode', 'rules', 'depth', 'output', 'tone', 'word_count', 'speech_fix']

/**
 * 返回与给定维度选择完全一致的预设配方 key；无匹配返回 ''
 * @param {Array} recipes 预设配方列表
 * @param {Record<string, string>} dims 维度选择 { dimKey: moduleKey }
 */
export function matchRecipeKey(recipes, dims) {
  if (!dims || !recipes?.length) return ''
  const match = recipes.find((r) =>
    r.dimensions && DIMENSION_KEYS
      .filter((k) => r.dimensions[k] !== undefined)
      .every((k) => (r.dimensions[k] || '') === (dims[k] || '')),
  )
  return match ? match.key : ''
}
