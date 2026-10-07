/**
 * 追问消息「按轮归位」纯函数（与后端 routers/chat.py `_get_session_history` 同一套规则）。
 *
 * 背景：重新生成任意一轮时，新变体行是 append 在会话末尾的（id 最大）。若按 id 顺序平铺渲染，
 * 中间轮的新版本会显示到最后一轮之后（跑位）。归位规则：
 *   - 变体组的「位置锚点」= 组内最早一行的 id（同组所有版本共享同一位置）；
 *   - 每行归属「锚点之前最近的一条提问行」所在轮；无组回答的锚点 = 自身 id。
 * 每轮只展示生效版本（组内无 is_active 行时兜底取最新），`variants` = 该组全部行（供 ‹N/M› 切换）。
 */

/** 组内最早一行 id（锚点）映射：variant_group → 最小 id */
function groupAnchors(rows) {
  const anchors = new Map()
  for (const r of rows) {
    const g = r.variant_group || ''
    if (!g) continue
    const cur = anchors.get(g)
    if (cur === undefined || r.id < cur) anchors.set(g, r.id)
  }
  return anchors
}

/** 锚点之前最近的一条提问行 id（无则 null） */
function ownerOf(anchor, userIds) {
  let owner = null
  for (const uid of userIds) {
    if (uid > anchor) break
    owner = uid
  }
  return owner
}

export function buildChatTurns(rows) {
  const list = Array.isArray(rows) ? rows : []
  const anchors = groupAnchors(list)
  const userIds = list.filter((r) => r.role === 'user').map((r) => r.id)

  const turns = new Map() // 提问行 id → { user, answers }
  const loose = [] // 前置无提问的孤立回答（异常数据兜底，不静默丢）
  for (const r of list) {
    const anchor = anchors.get(r.variant_group || '') ?? r.id
    if (r.role === 'user') {
      turns.set(r.id, { user: r, answers: [] })
      continue
    }
    const owner = ownerOf(anchor, userIds)
    if (owner === null) {
      loose.push(r)
      continue
    }
    if (!turns.has(owner)) turns.set(owner, { user: null, answers: [] })
    turns.get(owner).answers.push(r)
  }

  const out = []
  for (const { user, answers } of turns.values()) {
    if (user) out.push({ ...user, variants: [] })
    if (!answers.length) continue
    const active = answers.find((a) => a.is_active) || answers[answers.length - 1]
    out.push({ ...active, variants: answers })
  }
  for (const r of loose) out.push({ ...r, variants: [] })
  return out
}
