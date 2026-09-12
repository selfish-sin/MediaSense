/**
 * 内置预设背景：全部用 SVG 生成，作为 data-URI 注入 background-image。
 * 零版权、可缩放、体积小，且会随主题色（accent）联动。
 * 每个预设导出一个函数 (accentHex, opts) => dataURI，返回纯 CSS 可用的 url(...)。
 */

/** 转义 SVG 文本里的特殊字符，使其可安全内嵌进 data-URI。 */
function svgToDataUri(svg) {
  // 用 encodeURIComponent 比 base64 体积更小，且无 charset 歧义
  return `url("data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}")`
}

/** 取 hex 的 rgba 字符串（用于 SVG fill 透明度）。 */
function hexA(hex, a) {
  // 直接内联解析，避免循环依赖 color.js
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${a})`
}

/**
 * 1. 星河：深空底 + 疏密星点 + 一抹极光光带。
 * 最适合工具型深色界面，安静不喧宾夺主。
 */
export function bgStarry(accent = '#38BDF8') {
  // 随机但固定的星点坐标（伪随机，保证刷新一致）
  const stars = Array.from({ length: 140 }, (_, i) => {
    const x = (i * 73.31 % 100).toFixed(2)
    const y = (i * 41.17 % 100).toFixed(2)
    const r = ((i * 17) % 3) * 0.3 + 0.3
    const o = (0.2 + ((i * 13) % 8) * 0.1).toFixed(2)
    return `<circle cx="${x}" cy="${y}" r="${r.toFixed(2)}" fill="#fff" opacity="${o}"/>`
  }).join('')
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" preserveAspectRatio="xMidYMid slice">
<defs>
<radialGradient id="g" cx="50%" cy="20%" r="75%">
<stop offset="0%" stop-color="${hexA(accent, 0.10)}"/>
<stop offset="55%" stop-color="#0B1120" stop-opacity="0"/>
<stop offset="100%" stop-color="#060912"/>
</radialGradient>
<linearGradient id="aurora" x1="0" y1="0" x2="1" y2="1">
<stop offset="0%" stop-color="${hexA(accent, 0.22)}"/>
<stop offset="50%" stop-color="${hexA(accent, 0.05)}"/>
<stop offset="100%" stop-color="#0B1120" stop-opacity="0"/>
</linearGradient>
</defs>
<rect width="1440" height="900" fill="#070B16"/>
<rect width="1440" height="900" fill="url(#g)"/>
<path d="M-100,180 Q360,80 720,220 T1540,160 L1540,420 Q1080,520 720,360 T-100,440 Z" fill="url(#aurora)" opacity="0.6"/>
${stars}
</svg>`
  return svgToDataUri(svg)
}

/**
 * 2. 极光：多层流动的青蓝光带，垂直方向。
 * 比「星河」更浓烈，适合想要氛围感的场景。
 */
export function bgAurora(accent = '#38BDF8') {
  const bands = [
    { x: -200, w: 600, o: 0.18, dy: 0 },
    { x: 300, w: 700, o: 0.14, dy: 120 },
    { x: 800, w: 800, o: 0.12, dy: 60 },
    { x: 1100, w: 600, o: 0.16, dy: 200 },
  ]
  const paths = bands.map((b) => {
    const y = 200 + b.dy
    return `<path d="M${b.x},${y} C${b.x + b.w * 0.3},${y - 160} ${b.x + b.w * 0.6},${y + 200} ${b.x + b.w},${y} L${b.x + b.w},900 L${b.x},900 Z" fill="${hexA(accent, b.o)}"/>`
  }).join('')
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" preserveAspectRatio="xMidYMid slice">
<defs>
<linearGradient id="base" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#091022"/>
<stop offset="100%" stop-color="#05080F"/>
</linearGradient>
</defs>
<rect width="1440" height="900" fill="url(#base)"/>
${paths}
<circle cx="1180" cy="160" r="90" fill="${hexA(accent, 0.10)}" filter="blur(40px)"/>
<circle cx="260" cy="720" r="140" fill="${hexA(accent, 0.07)}" filter="blur(50px)"/>
</svg>`
  return svgToDataUri(svg)
}

/**
 * 3. 月夜远山：剪影山脉 + 月亮 + 星点。
 * 偏插画感，安静治愈。
 */
export function bgMoonlit(accent = '#38BDF8') {
  const stars = Array.from({ length: 60 }, (_, i) => {
    const x = (i * 83.7 % 100).toFixed(2)
    const y = (i * 29.5 % 55).toFixed(2)
    const r = ((i * 7) % 3) * 0.25 + 0.25
    return `<circle cx="${x}" cy="${y}" r="${r.toFixed(2)}" fill="#fff" opacity="0.5"/>`
  }).join('')
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" preserveAspectRatio="xMidYMid slice">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#0A1428"/>
<stop offset="60%" stop-color="#0B1830"/>
<stop offset="100%" stop-color="#0E2038"/>
</linearGradient>
<radialGradient id="moon" cx="50%" cy="50%" r="50%">
<stop offset="0%" stop-color="#E6F3FF"/>
<stop offset="70%" stop-color="#BFDCFA"/>
<stop offset="100%" stop-color="${hexA(accent, 0.0)}"/>
</radialGradient>
</defs>
<rect width="1440" height="900" fill="url(#sky)"/>
${stars}
<circle cx="1080" cy="220" r="70" fill="url(#moon)"/>
<circle cx="1080" cy="220" r="110" fill="${hexA(accent, 0.06)}"/>
<path d="M0,680 L160,560 L320,640 L480,520 L640,610 L820,500 L1000,600 L1180,510 L1340,580 L1440,560 L1440,900 L0,900 Z" fill="#0A1626"/>
<path d="M0,760 L200,680 L400,740 L600,660 L820,730 L1040,670 L1260,740 L1440,700 L1440,900 L0,900 Z" fill="#07101E"/>
<path d="M0,840 L240,800 L480,830 L760,790 L1040,825 L1300,795 L1440,815 L1440,900 L0,900 Z" fill="#040810"/>
</svg>`
  return svgToDataUri(svg)
}

/**
 * 4. 深海：柔和的青蓝光晕，干净简洁，最不抢戏。
 * 适合长时间专注工作。
 */
export function bgAbyss(accent = '#38BDF8') {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" preserveAspectRatio="xMidYMid slice">
<defs>
<radialGradient id="c1" cx="20%" cy="25%" r="55%">
<stop offset="0%" stop-color="${hexA(accent, 0.16)}"/>
<stop offset="100%" stop-color="${hexA(accent, 0)}"/>
</radialGradient>
<radialGradient id="c2" cx="85%" cy="80%" r="60%">
<stop offset="0%" stop-color="${hexA(accent, 0.10)}"/>
<stop offset="100%" stop-color="${hexA(accent, 0)}"/>
</radialGradient>
<linearGradient id="base" x1="0" y1="0" x2="1" y2="1">
<stop offset="0%" stop-color="#08111F"/>
<stop offset="100%" stop-color="#050A14"/>
</linearGradient>
</defs>
<rect width="1440" height="900" fill="url(#base)"/>
<rect width="1440" height="900" fill="url(#c1)"/>
<rect width="1440" height="900" fill="url(#c2)"/>
</svg>`
  return svgToDataUri(svg)
}

/**
 * 5. 网格：科技感网格 + 光点，干净理性。
 */
export function bgGrid(accent = '#38BDF8') {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" preserveAspectRatio="xMidYMid slice">
<defs>
<linearGradient id="base" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#08111F"/>
<stop offset="100%" stop-color="#050A14"/>
</linearGradient>
<radialGradient id="glow" cx="50%" cy="40%" r="60%">
<stop offset="0%" stop-color="${hexA(accent, 0.12)}"/>
<stop offset="100%" stop-color="${hexA(accent, 0)}"/>
</radialGradient>
<pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
<path d="M60,0 L0,0 L0,60" fill="none" stroke="${hexA(accent, 0.06)}" stroke-width="1"/>
</pattern>
</defs>
<rect width="1440" height="900" fill="url(#base)"/>
<rect width="1440" height="900" fill="url(#grid)"/>
<rect width="1440" height="900" fill="url(#glow)"/>
<circle cx="120" cy="120" r="2" fill="${hexA(accent, 0.6)}"/>
<circle cx="1320" cy="240" r="2" fill="${hexA(accent, 0.5)}"/>
<circle cx="780" cy="780" r="2" fill="${hexA(accent, 0.55)}"/>
</svg>`
  return svgToDataUri(svg)
}

/**
 * 预设清单（供面板渲染）。type 决定 store 如何生成 dataURI。
 * 内置预设 type 为 'builtin'，自定义上传 type 为 'custom'。
 */
export const BUILTIN_BACKGROUNDS = [
  { id: 'starry', name: '星河', render: bgStarry, accent: '#38BDF8' },
  { id: 'aurora', name: '极光', render: bgAurora, accent: '#38BDF8' },
  { id: 'moonlit', name: '月夜远山', render: bgMoonlit, accent: '#7CB9E8' },
  { id: 'abyss', name: '深海光晕', render: bgAbyss, accent: '#22D3EE' },
  { id: 'grid', name: '科技网格', render: bgGrid, accent: '#38BDF8' },
]

/** 由预设 id + 当前主色 生成 dataURI。 */
export function renderBuiltin(id, accent) {
  const preset = BUILTIN_BACKGROUNDS.find((b) => b.id === id)
  return preset ? preset.render(accent) : bgAbyss(accent)
}
