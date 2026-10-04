/**
 * Catppuccin 皮肤（4 个 flavor）——palette 精简模式。
 *
 * 色值取自 Catppuccin 官方调色板（github.com/catppuccin/catppuccin），
 * 语义命名沿用其风格：crust < mantle < base < surface0-2 < overlay0-2、
 * text / subtext1 / subtext0、以及红绿黄蓝青桃等强调色。
 *
 * 只声明 18 个原始色，其余 50+ 个 `--app-*` token 由 skin.js 的
 * DERIVE_RULES 表驱动派生——这是「新增皮肤只改数据、不改组件」的关键。
 *
 * 四套共用 family='catppuccin'，使「日间/夜间」按钮在本族内切换（Mocha ⇄ Latte）。
 */

/** 四个 flavor 的调色板（键名统一，便于派生规则复用）。 */
const PALETTES = {
  mocha: {
    crust: '#11111b', mantle: '#181825', base: '#1e1e2e',
    surface0: '#313244', surface1: '#45475a', surface2: '#585b70',
    overlay0: '#6c7086', overlay1: '#7f849c', overlay2: '#9399b2',
    text: '#cdd6f4', subtext1: '#bac2de', subtext0: '#a6adc8',
    red: '#f38ba8', green: '#a6e3a1', yellow: '#f9e2af',
    blue: '#89b4fa', teal: '#94e2d5', peach: '#fab387', mauve: '#cba6f7',
  },
  macchiato: {
    crust: '#181926', mantle: '#1e2030', base: '#24273a',
    surface0: '#363a4f', surface1: '#494d64', surface2: '#5b6078',
    overlay0: '#6e738d', overlay1: '#8087a2', overlay2: '#939ab7',
    text: '#cad3f5', subtext1: '#b8c0e0', subtext0: '#a5adcb',
    red: '#ed8796', green: '#a6da95', yellow: '#eed49f',
    blue: '#8aadf4', teal: '#8bd5ca', peach: '#f5a97f', mauve: '#c6a0f6',
  },
  frappe: {
    crust: '#232634', mantle: '#292c3c', base: '#303446',
    surface0: '#414559', surface1: '#51576d', surface2: '#626880',
    overlay0: '#737994', overlay1: '#838ba7', overlay2: '#949cbb',
    text: '#c6d0f5', subtext1: '#b5bfe2', subtext0: '#a5adce',
    red: '#e78284', green: '#a6d189', yellow: '#e5c890',
    blue: '#8caaee', teal: '#81c8be', peach: '#ef9f76', mauve: '#ca9ee6',
  },
  latte: {
    crust: '#dce0e8', mantle: '#e6e9ef', base: '#eff1f5',
    surface0: '#ccd0da', surface1: '#bcc0cc', surface2: '#acb0be',
    overlay0: '#9ca0b0', overlay1: '#8c8fa1', overlay2: '#7c7f93',
    text: '#4c4f69', subtext1: '#5c5f77', subtext0: '#6c6f85',
    red: '#d20f39', green: '#40a02b', yellow: '#df8e1d',
    blue: '#1e66f5', teal: '#179299', peach: '#fe640b', mauve: '#8839ef',
  },
}

/** 生成一套皮肤对象（减少重复样板）。 */
function makeSkin({ id, name, flavor, mode }) {
  const palette = PALETTES[flavor]
  return {
    id,
    name,
    family: 'catppuccin',
    mode,
    // 换肤时 themeStore 会把主色切到皮肤自带色（mauve 是 Catppuccin 的招牌强调色）
    accent: palette.mauve,
    palette,
  }
}

export const catppuccinMocha = makeSkin({ id: 'catppuccin-mocha', name: 'Catppuccin Mocha', flavor: 'mocha', mode: 'dark' })
export const catppuccinMacchiato = makeSkin({ id: 'catppuccin-macchiato', name: 'Catppuccin Macchiato', flavor: 'macchiato', mode: 'dark' })
export const catppuccinFrappe = makeSkin({ id: 'catppuccin-frappe', name: 'Catppuccin Frappé', flavor: 'frappe', mode: 'dark' })
export const catppuccinLatte = makeSkin({ id: 'catppuccin-latte', name: 'Catppuccin Latte', flavor: 'latte', mode: 'light' })

export const CATPPUCCIN_SKINS = [
  catppuccinMocha,
  catppuccinMacchiato,
  catppuccinFrappe,
  catppuccinLatte,
]
