import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { deriveAccentPair, hexToRgb } from '../utils/color'
import { renderBuiltin, renderAnimated, getAnimatedPreset } from '../utils/backgrounds'
import { resolveSkin, skinMode, composeTokens, writeTokens, skinForMode } from '../utils/skin'
import { BUILTIN_SKINS, DEFAULT_SKIN_ID } from '../utils/skins'
import { normalizeSkin, skinToJson } from '../utils/skinSchema'
import { DEFAULT_EFFECTS, effectVars, loadEffects } from '../utils/effects'
import { prepareBackgroundImage, blobToDataUrl } from '../utils/image'
import {
  addBackground as idbAdd,
  deleteBackground as idbDelete,
  getBackground as idbGet,
  isAvailable as idbAvailable,
  libraryUsage as idbUsage,
  listBackgrounds as idbList,
} from '../utils/bgStore'

/**
 * 主题管理：皮肤 + 效果 + 主色调 + 背景媒体（图库/动态）。
 *
 * - skinId: 当前皮肤 id（内置或用户导入）——决定整套配色与明暗分支
 * - effects: 氛围参数（遮罩/暗角/玻璃/透明度）——决定观感
 * - accent: 主色 hex——换肤时取皮肤自带主色，用户可再手动微调
 * - background: 背景媒体，四类
 *     { type:'none' } | { type:'builtin', id } | { type:'animated', preset }
 *     | { type:'image', assetId, thumbUrl? }
 *   兼容旧字段 { type:'custom', url }（dataURL）——启动时迁移进图库
 * - library: IndexedDB 图库列表（元数据，不含原图）
 *
 * 应用顺序 applyVisuals()：皮肤 tokens → data-theme → 效果变量 → accent。
 * 背景与配色解耦：背景只负责 `--app-bg-image` 与动态背景属性。
 */

const SKIN_KEY = 'mediasense_skin'
const USER_SKINS_KEY = 'mediasense_user_skins'
const EFFECTS_KEY = 'mediasense_effects'
const ACCENT_KEY = 'mediasense_accent'
const BG_KEY = 'mediasense_background'

/** 午夜青蓝默认主色。 */
const DEFAULT_ACCENT = '#38BDF8'

/** 背景媒体默认值（效果参数已迁出到 effects，见 utils/effects.js）。 */
const DEFAULT_BG = { type: 'builtin', id: 'abyss' }

/** visibilitychange 只绑一次（HMR / 多次初始化不重复注册监听）。 */
let visibilityBound = false

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch {
    return fallback
  }
}

export const useThemeStore = defineStore('theme', () => {
  const skinId = ref(localStorage.getItem(SKIN_KEY) || DEFAULT_SKIN_ID)
  const accent = ref(localStorage.getItem(ACCENT_KEY) || DEFAULT_ACCENT)
  const background = ref(loadJson(BG_KEY, { ...DEFAULT_BG }))
  const effects = ref(loadEffects(loadJson(EFFECTS_KEY, null), background.value))
  const userSkins = ref(loadJson(USER_SKINS_KEY, []) || [])

  // —— 背景图库状态 ——
  /** 图库元数据列表（不含原图，避免把几十 MB 读进内存）。 */
  const library = ref([])
  /** 图库用量 {count, bytes}。 */
  const usage = ref({ count: 0, bytes: 0 })
  /** IndexedDB 是否可用（不可用时整体走 localStorage 降级路径）。 */
  const libraryReady = ref(false)
  /** 背景相关的**可见**提示（迁移失败/降级/读取失败/容量超限）。 */
  const bgNotice = ref('')
  /** 持久化失败原因（配额溢出等）——必须可见，否则"设置没保存"变成看不见的幽灵问题 */
  const storageError = ref('')

  /** 当前渲染用的 objectURL：**唯一创建点 + 唯一 revoke 点**（都在本 store）。 */
  let currentObjectUrl = null
  function revokeCurrentUrl() {
    if (currentObjectUrl) {
      try { URL.revokeObjectURL(currentObjectUrl) } catch { /* 已失效 */ }
      currentObjectUrl = null
    }
  }

  // —— 皮肤派生状态 ——
  const availableSkins = computed(() => [...BUILTIN_SKINS, ...userSkins.value])
  const skin = computed(() => resolveSkin(skinId.value, userSkins.value))
  const theme = computed(() => skinMode(skin.value))

  function persist(key, value) {
    try {
      localStorage.setItem(key, value)
      storageError.value = ''
      return true
    } catch (err) {
      // 不静默吞：localStorage 配额约 5MB
      console.error('[themeStore] 持久化失败:', key, err)
      storageError.value = err?.name === 'QuotaExceededError'
        ? '浏览器本地存储已满，本次设置不会被保存'
        : '本地存储写入失败，本次设置不会被保存'
      return false
    }
  }

  /** 把一个 hex 主色写成 --app-accent-1/2 及 -rgb 三元组到 :root。 */
  function applyAccent(hex) {
    const { accent1, accent2 } = deriveAccentPair(hex)
    const root = document.documentElement
    root.style.setProperty('--app-accent-1', accent1)
    root.style.setProperty('--app-accent-2', accent2)
    const rgb1 = hexToRgb(accent1)
    const rgb2 = hexToRgb(accent2)
    if (rgb1) root.style.setProperty('--app-accent-1-rgb', rgb1.join(', '))
    if (rgb2) root.style.setProperty('--app-accent-2-rgb', rgb2.join(', '))
    root.style.setProperty('--app-accent', accent1)
    const rgbA = hexToRgb(accent1)
    if (rgbA) root.style.setProperty('--app-accent-rgb', rgbA.join(', '))
  }

  function applyTheme(t) {
    const html = document.documentElement
    if (t === 'dark') {
      html.setAttribute('data-theme', 'dark')
      html.classList.add('dark')
    } else {
      html.setAttribute('data-theme', 'light')
      html.classList.remove('dark')
    }
  }

  /** 动态背景：只切 1 个属性 + 2 个变量，动画本身在 CSS 里跑（省电、可暂停）。 */
  function applyBgAnimation(root, anim) {
    if (anim) {
      root.setAttribute('data-bg-anim', anim.id)
      root.style.setProperty('--app-bg-anim-name', anim.animation)
      root.style.setProperty('--app-bg-anim-duration', `${anim.duration}s`)
    } else {
      root.removeAttribute('data-bg-anim')
      root.style.removeProperty('--app-bg-anim-name')
      root.style.removeProperty('--app-bg-anim-duration')
    }
  }

  /**
   * 应用背景媒体。
   * - builtin / animated：同步（程序化 SVG dataURI）
   * - image：先上 thumbUrl 占位（几十 KB），再异步换成 IndexedDB 里的原图
   * - custom：旧 dataURL 路径（迁移前 / IndexedDB 不可用时保留，不删）
   */
  async function applyBackground(bg) {
    const root = document.documentElement
    let url = 'none'
    let anim = null

    if (bg.type === 'builtin') {
      url = renderBuiltin(bg.id, accent.value)
    } else if (bg.type === 'animated') {
      anim = getAnimatedPreset(bg.preset)
      url = renderAnimated(bg.preset, accent.value)
    } else if (bg.type === 'image') {
      url = bg.thumbUrl ? `url("${bg.thumbUrl}")` : 'none'
    } else if (bg.type === 'custom' && bg.url) {
      url = `url("${bg.url}")`
    }

    root.style.setProperty('--app-bg-image', url)
    applyBgAnimation(root, anim)

    if (bg.type !== 'image' || !bg.assetId || !libraryReady.value) return

    const assetId = bg.assetId
    try {
      const record = await idbGet(assetId)
      // 竞态保护：等待期间用户可能已切走背景/换了图
      if (background.value.assetId !== assetId) return
      if (!record?.blob) {
        bgNotice.value = '所选背景图已不存在（可能已被清理），请重新选择'
        root.style.setProperty('--app-bg-image', 'none')
        return
      }
      revokeCurrentUrl()
      currentObjectUrl = URL.createObjectURL(record.blob)
      root.style.setProperty('--app-bg-image', `url("${currentObjectUrl}")`)
    } catch (err) {
      bgNotice.value = `背景图读取失败：${err?.message || '未知错误'}`
    }
  }

  /** 把当前皮肤 + 效果完整应用到 :root（accent 最后写，用户主色优先）。 */
  function applyVisuals() {
    writeTokens(composeTokens(skin.value))
    applyTheme(theme.value)
    writeTokens(effectVars(effects.value, theme.value))
    applyAccent(accent.value)
  }

  // —— 背景图库 ——

  /** 读取图库列表与用量（失败给可见提示，不清空已有列表）。 */
  async function loadLibrary() {
    if (!libraryReady.value) return
    try {
      library.value = await idbList()
      usage.value = await idbUsage()
    } catch (err) {
      bgNotice.value = `图库读取失败：${err?.message || '未知错误'}`
    }
  }

  /**
   * 迁移旧版背景（dataURL 存 localStorage → 图库）。
   * **write-then-clean**：确认写入成功才清理旧字段；失败一律保留旧数据继续渲染。
   * 幂等：迁移后 background.type 变为 'image'，下次启动不再触发。
   */
  async function migrateLegacyBackground() {
    const bg = background.value
    if (bg.type !== 'custom' || !bg.url || !libraryReady.value) return
    try {
      const res = await fetch(bg.url)
      const blob = await res.blob()
      const id = `bg-migrated-${Date.now()}`
      const added = await idbAdd({
        id,
        name: '迁移的背景',
        blob,
        // ★ 不回写大 dataURL 到 localStorage（那正是要摆脱的东西）
        thumbUrl: '',
        width: 0,
        height: 0,
        bytes: blob.size,
      })
      if (!added.ok) {
        bgNotice.value = `旧背景归档失败：${added.error}。背景仍可正常显示，建议重新上传`
        return
      }
      background.value = { type: 'image', assetId: id }
      await loadLibrary()
      bgNotice.value = '已把原有背景图归档到本地图库'
    } catch (err) {
      bgNotice.value = `旧背景归档失败：${err?.message || '读取图片失败'}。背景仍可正常显示，建议重新上传`
    }
  }

  /** 上传一张背景图（压缩 → 分析 → 入库 → 应用）。失败返回可行动原因。 */
  async function uploadBackground(file) {
    const prepared = await prepareBackgroundImage(file)

    // 降级：IndexedDB 不可用 → 走 localStorage 旧路径
    if (!libraryReady.value) {
      const dataUrl = await blobToDataUrl(prepared.blob)
      background.value = { type: 'custom', url: dataUrl }
      return { ok: true, mode: 'legacy', ...prepared }
    }

    const id = `bg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    const added = await idbAdd({
      id,
      name: file.name || '背景图',
      blob: prepared.blob,
      thumbUrl: prepared.thumbUrl,
      width: prepared.width,
      height: prepared.height,
      bytes: prepared.bytes,
    })
    if (!added.ok) return added

    await loadLibrary()
    background.value = { type: 'image', assetId: id, thumbUrl: prepared.thumbUrl }
    return { ok: true, mode: 'library', id, ...prepared }
  }

  /** 切换到图库里的某张图。 */
  function selectBackground(assetId) {
    const item = library.value.find((x) => x.id === assetId)
    background.value = { type: 'image', assetId, thumbUrl: item?.thumbUrl || '' }
  }

  /** 从图库删除一张图；若正在用它则回退无背景。 */
  async function removeBackground(assetId) {
    if (!libraryReady.value) return { ok: false, error: '当前环境无法使用本地图库' }
    try {
      await idbDelete(assetId)
    } catch (err) {
      return { ok: false, error: `删除失败：${err?.message || '未知错误'}` }
    }
    await loadLibrary()
    if (background.value.assetId === assetId) {
      revokeCurrentUrl()
      background.value = { type: 'none' }
    }
    return { ok: true }
  }

  /** 启动：探测图库 → 载入列表 → 迁移旧数据 → 应用背景。 */
  async function initBackground() {
    libraryReady.value = await idbAvailable()
    if (!libraryReady.value) {
      bgNotice.value = '当前环境无法使用本地图库（可能是隐私模式），背景图会按旧方式保存'
      await applyBackground(background.value)
      return
    }
    await loadLibrary()
    await migrateLegacyBackground()
    await applyBackground(background.value)
  }

  /** 动态背景失焦暂停：只切一个属性，动画本身在 CSS 里（省电）。 */
  function bindVisibilityPause() {
    if (visibilityBound || typeof document === 'undefined') return
    visibilityBound = true
    document.addEventListener('visibilitychange', () => {
      const root = document.documentElement
      if (document.hidden) root.setAttribute('data-bg-paused', '1')
      else root.removeAttribute('data-bg-paused')
    })
  }

  // 初始化
  applyVisuals()
  bindVisibilityPause()
  initBackground() // 异步，不阻塞首屏

  // —— 皮肤 / 明暗分支 ——
  /**
   * 切换皮肤。换肤即换主色（皮肤自带 accent 优先），
   * 用户随后可在面板里手动微调——符合"换皮肤=换整套配色"的直觉。
   */
  function setSkin(id) {
    skinId.value = id
    const target = resolveSkin(id, userSkins.value)
    if (target?.accent) accent.value = target.accent
  }
  /** 切到指定明暗分支——在同 family 内换肤，无同族则回退默认皮肤。 */
  function setTheme(t) {
    setSkin(skinForMode(skin.value, t).id)
  }
  function toggle() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  /**
   * 导入一份皮肤 JSON 文本。
   * @returns {{ok: true, skin: object} | {ok: false, error: string}} 失败原因可行动
   */
  function importSkin(jsonText) {
    let parsed
    try {
      parsed = JSON.parse(jsonText)
    } catch (err) {
      return { ok: false, error: `文件不是合法 JSON：${err?.message || '解析失败'}` }
    }
    const result = normalizeSkin(parsed)
    if (!result.ok) return result
    const next = result.skin
    if (BUILTIN_SKINS.some((s) => s.id === next.id)) {
      return { ok: false, error: `id "${next.id}" 与内置皮肤冲突，请修改文件中的 id 后重试` }
    }
    // 同 id 覆盖式导入（便于更新自己做的皮肤）
    userSkins.value = [...userSkins.value.filter((s) => s.id !== next.id), next]
    persist(USER_SKINS_KEY, JSON.stringify(userSkins.value))
    if (skinId.value === next.id) applyVisuals()
    return { ok: true, skin: next }
  }

  /** 导出皮肤为 JSON 文本（用于下载/分享）。 */
  function exportSkinJson(id) {
    return skinToJson(resolveSkin(id, userSkins.value))
  }

  // —— 主色调 ——
  function setAccent(hex) {
    accent.value = hex
  }

  // —— 效果 ——
  function setEffect(partial) {
    effects.value = { ...effects.value, ...partial }
  }
  function resetEffects() {
    effects.value = { ...DEFAULT_EFFECTS }
  }

  // —— 背景（程序化 / 动态 / 无） ——
  /**
   * **替换**而非字段合并：背景类型只能有一个，
   * 合并会让上一类的字段（如 builtin 的 id、image 的 assetId）残留下来。
   */
  function setBackground(next) {
    background.value = { ...next }
  }
  function resetBackground() {
    background.value = { ...DEFAULT_BG }
  }

  // 监听并持久化
  watch(skinId, (id) => {
    applyVisuals()
    persist(SKIN_KEY, id)
  })

  // 效果参数变化只需重写效果变量（皮肤 60+ 项不动，避免拖滑块时全量重算样式）
  watch(effects, (e) => {
    writeTokens(effectVars(e, theme.value))
    persist(EFFECTS_KEY, JSON.stringify(e))
  }, { deep: true })

  watch(accent, (hex) => {
    applyAccent(hex)
    persist(ACCENT_KEY, hex)
    // 程序化背景跟随主色重渲染（图库图不联动）
    const t = background.value.type
    if (t === 'builtin' || t === 'animated') applyBackground(background.value)
  })

  watch(background, (bg) => {
    applyBackground(bg)
    persist(BG_KEY, JSON.stringify(bg))
  }, { deep: true })

  return {
    skinId, skin, theme, accent, background, effects, storageError,
    availableSkins, userSkins,
    // 背景图库
    library, usage, libraryReady, bgNotice,
    loadLibrary, uploadBackground, selectBackground, removeBackground, initBackground,
    // 皮肤
    toggle, setTheme, setSkin, importSkin, exportSkinJson,
    setAccent,
    setEffect, resetEffects,
    setBackground, resetBackground,
  }
})
