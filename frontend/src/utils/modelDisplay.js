/**
 * 模型显示名：有引擎时显示「引擎-名称」（如 阿里云-qwen-plus），无引擎直接显示名称
 * 所有展示模型名的地方统一走这里，保证「引擎-模型」命名一致
 */
export function modelDisplayName(m) {
  if (!m) return ''
  const engine = (m.engine || '').trim()
  return engine ? `${engine}-${m.name}` : (m.name || '')
}