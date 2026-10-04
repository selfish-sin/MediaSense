/**
 * 皮肤 JSON 的校验与归一化（导入用）。
 *
 * 原则：非法输入必须给出**可行动**的原因——不静默失败、不半途应用半套皮肤。
 * 归一化后只保留白名单字段，防止外部文件往皮肤里夹带任意数据。
 */

const ID_RE = /^[a-z0-9][a-z0-9-]{1,48}$/
const HEX_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/
const MODES = ['dark', 'light']

/** palette 必需色（DERIVE_RULES 依赖这些键；缺任一都会派生出错误配色）。 */
export const REQUIRED_PALETTE_KEYS = [
  'crust', 'mantle', 'base',
  'surface0', 'surface1', 'surface2',
  'overlay0', 'text', 'subtext1', 'subtext0',
  'red', 'green', 'yellow', 'blue', 'teal', 'peach',
]

const fail = (error) => ({ ok: false, error })

/** 供下载的导出形态：包一层 `skin`，便于将来在同一文件里放作者/版本等元数据。 */
export function skinToJson(skin) {
  return JSON.stringify({
    skin: {
      id: skin.id,
      name: skin.name,
      mode: skin.mode,
      family: skin.family || 'custom',
      accent: skin.accent,
      palette: skin.palette,
      tokens: skin.tokens,
    },
  }, null, 2)
}

/**
 * 校验并归一化一份皮肤数据。
 * 接受两种输入：完整皮肤对象，或 `{ skin: {...} }` 包装（导出文件的形态）。
 * @returns {{ok: true, skin: object} | {ok: false, error: string}}
 */
export function normalizeSkin(raw) {
  const input = (raw && typeof raw === 'object' && !Array.isArray(raw) && raw.skin) ? raw.skin : raw
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return fail('文件内容不是皮肤对象（应为 JSON 对象）')
  }

  const id = typeof input.id === 'string' ? input.id.trim() : ''
  if (!ID_RE.test(id)) {
    return fail('皮肤 id 非法：需为 2-49 位小写字母/数字/连字符，且以字母或数字开头')
  }

  const name = typeof input.name === 'string' ? input.name.trim() : ''
  if (!name) return fail('皮肤 name 不能为空')
  if (name.length > 60) return fail('皮肤 name 过长（上限 60 字符）')

  if (!MODES.includes(input.mode)) {
    return fail(`皮肤 mode 非法：应为 dark 或 light（当前 ${JSON.stringify(input.mode)}）`)
  }

  const hasPalette = input.palette && typeof input.palette === 'object' && !Array.isArray(input.palette)
  const hasTokens = input.tokens && typeof input.tokens === 'object' && !Array.isArray(input.tokens)
  if (!hasPalette && !hasTokens) {
    return fail('皮肤必须至少提供 palette（精简色板）或 tokens（完整 token）之一')
  }

  const skin = {
    id,
    name,
    mode: input.mode,
    family: (typeof input.family === 'string' && input.family.trim()) ? input.family.trim() : 'custom',
  }

  if (input.accent !== undefined && input.accent !== null && input.accent !== '') {
    if (typeof input.accent !== 'string' || !HEX_RE.test(input.accent.trim())) {
      return fail(`accent 不是合法 hex 颜色：${JSON.stringify(input.accent)}`)
    }
    skin.accent = input.accent.trim()
  }

  if (hasPalette) {
    const palette = {}
    const missing = []
    for (const key of REQUIRED_PALETTE_KEYS) {
      const value = input.palette[key]
      if (typeof value === 'string' && HEX_RE.test(value.trim())) palette[key] = value.trim()
      else missing.push(key)
    }
    if (missing.length) {
      return fail(`palette 缺少或格式错误的必需色：${missing.join('、')}（共需 ${REQUIRED_PALETTE_KEYS.length} 个）`)
    }
    // 允许额外自定义色（如 mauve），合法即保留
    for (const [key, value] of Object.entries(input.palette)) {
      if (typeof value === 'string' && HEX_RE.test(value.trim())) palette[key] = value.trim()
    }
    skin.palette = palette
  }

  if (hasTokens) {
    const tokens = {}
    for (const [key, value] of Object.entries(input.tokens)) {
      if (typeof key !== 'string' || !key.startsWith('--')) {
        return fail(`token 名必须以 -- 开头：${JSON.stringify(key)}`)
      }
      if (typeof value !== 'string' && typeof value !== 'number') {
        return fail(`token ${key} 的值必须是字符串或数字`)
      }
      tokens[key] = String(value)
    }
    skin.tokens = tokens
  }

  return { ok: true, skin }
}
