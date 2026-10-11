/**
 * 维度配方工具（SplitPromptPanel 与 DimensionSelector 共用）
 */

// 维度列表（带顺序：mode 放在 rules 之前；language 在 depth 之后；speech_fix 追加在末尾）
// ★ 2026-10-11：word_count 已并入 depth（字数随档位），从维度列表移除
// matchRecipeKey 只比较配方定义过的维度——配方未定义的维度不影响高亮，新增维度不会让已有配方失效
export const DIMENSION_KEYS = ['role', 'mode', 'content_type', 'rules', 'depth', 'output', 'tone', 'language', 'speech_fix']

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
