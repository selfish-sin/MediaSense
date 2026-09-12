/**
 * 维度配方工具（SplitPromptPanel 与 DimensionSelector 共用）
 */

// 维度列表（带顺序：mode 放在 rules 之前；language 在 word_count 之后）
// ★ 内置配方（后端 RECIPES）只定义前 8 个维度、不含 language——matchRecipeKey
//   会跳过配方未定义的维度，避免新增维度后所有配方高亮失效
export const DIMENSION_KEYS = ['role', 'mode', 'content_type', 'rules', 'depth', 'output', 'tone', 'word_count', 'language']

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
