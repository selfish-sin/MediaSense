/**
 * 背景图库：IndexedDB 存 **Blob**。
 *
 * 为什么不用 localStorage：配额实测只有 ~5MB 且按 UTF-16 字符计，
 * 一张 4MB 图的 dataURL ≈ 5.5M 字符直接写不进去（本项目已实测踩坑，
 * 见 utils/image.js 的压缩兜底）。IndexedDB 无 base64 膨胀、配额按磁盘量级。
 *
 * 记录结构：
 *   { id, name, blob, thumbUrl, width, height, bytes, addedAt, avgLuma }
 * - `blob`     原图（渲染用，只有 `getBackground` 会返回）
 * - `thumbUrl` 小尺寸 dataURL（图库网格直接当 background-image 用，
 *   **故意不用 objectURL**，避免每张缩略图都要管生命周期）
 *
 * 事务约定（易踩坑）：
 * - **写**操作必须等 `transaction.oncomplete` 才 resolve（否则可能在 commit 前就 close，
 *   数据是否落库取决于实现细节）；
 * - **读**操作在 request 成功时即可 resolve（只读事务无提交语义）。
 *
 * 所有函数接收可注入的 `idbFactory`（默认 window.indexedDB），便于单测注入 fake。
 */

const DB_NAME = 'mediasense-media'
const DB_VERSION = 1
const STORE = 'backgrounds'

/** 图库上限：张数与总字节双限，任一超限即拒绝新增并给可行动提示。 */
export const LIBRARY_LIMITS = { maxCount: 24, maxBytes: 120 * 1024 * 1024 }

function resolveFactory(factory) {
  if (factory) return factory
  return (typeof indexedDB !== 'undefined' && indexedDB) ? indexedDB : null
}

/** 打开（必要时创建）数据库。环境不支持时 reject 可读原因。 */
export function openDb(factory) {
  const idb = resolveFactory(factory)
  if (!idb) return Promise.reject(new Error('当前环境不支持 IndexedDB'))
  return new Promise((resolve, reject) => {
    let req
    try {
      req = idb.open(DB_NAME, DB_VERSION)
    } catch (err) {
      reject(err)
      return
    }
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'id' })
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error || new Error('IndexedDB 打开失败'))
  })
}

/** 图库是否可用（隐私模式/被禁用时返回 false，调用方据此走降级路径）。 */
export function isAvailable(factory) {
  return openDb(factory)
    .then((db) => { db.close(); return true })
    .catch(() => false)
}

/** 开启一个事务并保证 db 被关闭。 */
function runTx(factory, mode, body) {
  return openDb(factory).then((db) => new Promise((resolve, reject) => {
    let tx
    try {
      tx = db.transaction(STORE, mode)
    } catch (err) {
      db.close()
      reject(err)
      return
    }
    const settle = (ok, value) => {
      try { db.close() } catch { /* 已关闭 */ }
      if (ok) resolve(value)
      else reject(value)
    }
    tx.oncomplete = () => settle(true, undefined)
    tx.onabort = () => settle(false, tx.error || new Error('IndexedDB 事务中止'))
    try {
      body(tx.objectStore(STORE), settle)
    } catch (err) {
      settle(false, err)
    }
  }))
}

/**
 * 列出图库（**不含 blob**，只留元数据 + 缩略图），按 addedAt 倒序（新的在前）。
 * 用游标逐条读，避免一次性把所有原图读进内存。
 */
export function listBackgrounds(factory) {
  return openDb(factory).then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly')
    const out = []
    const req = tx.objectStore(STORE).openCursor()
    req.onsuccess = () => {
      const cursor = req.result
      if (!cursor) {
        db.close()
        out.sort((a, b) => (b.addedAt || 0) - (a.addedAt || 0))
        resolve(out)
        return
      }
      const { blob, ...meta } = cursor.value
      void blob
      out.push(meta)
      cursor.continue()
    }
    req.onerror = () => { db.close(); reject(req.error) }
    tx.onabort = () => reject(tx.error || new Error('IndexedDB 事务中止'))
  }))
}

/** 取单条完整记录（含 blob）；不存在返回 null。 */
export function getBackground(id, factory) {
  return openDb(factory).then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly')
    const req = tx.objectStore(STORE).get(id)
    req.onsuccess = () => { db.close(); resolve(req.result || null) }
    req.onerror = () => { db.close(); reject(req.error) }
    tx.onabort = () => reject(tx.error || new Error('IndexedDB 事务中止'))
  }))
}

/** 写入/覆盖一条记录（等事务提交完成才 resolve）。 */
export function putBackground(record, factory) {
  return runTx(factory, 'readwrite', (store) => store.put(record)).then(() => record)
}

/** 删除一条记录（不存在也算成功，幂等）。 */
export function deleteBackground(id, factory) {
  return runTx(factory, 'readwrite', (store) => store.delete(id)).then(() => true)
}

/** 统计用量：张数与总字节（bytes 缺失时用 blob.size 兜底）。 */
export function libraryUsage(factory) {
  return openDb(factory).then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly')
    let count = 0
    let bytes = 0
    const req = tx.objectStore(STORE).openCursor()
    req.onsuccess = () => {
      const cursor = req.result
      if (!cursor) {
        db.close()
        resolve({ count, bytes })
        return
      }
      count += 1
      bytes += cursor.value.bytes || cursor.value.blob?.size || 0
      cursor.continue()
    }
    req.onerror = () => { db.close(); reject(req.error) }
    tx.onabort = () => reject(tx.error || new Error('IndexedDB 事务中止'))
  }))
}

/**
 * 容量校验（**纯函数**，便于单测）。
 * @returns {{ok: true} | {ok: false, error: string}} 超限时 error 可行动
 */
export function checkCapacity(usage, incomingBytes, limits = LIBRARY_LIMITS) {
  const count = usage?.count || 0
  const currentBytes = usage?.bytes || 0
  const incoming = Number(incomingBytes) || 0
  if (count + 1 > limits.maxCount) {
    return { ok: false, error: `图库最多存 ${limits.maxCount} 张背景，请先删除不用的` }
  }
  if (currentBytes + incoming > limits.maxBytes) {
    const mb = Math.round(limits.maxBytes / 1024 / 1024)
    return { ok: false, error: `图库总容量上限 ${mb}MB，请先删除不用的背景` }
  }
  return { ok: true }
}

/**
 * 高层入口：先校验容量再写入。
 * @returns {{ok: true, record} | {ok: false, error}}
 */
export async function addBackground(payload, factory) {
  const usage = await libraryUsage(factory)
  const bytes = payload?.bytes || payload?.blob?.size || 0
  const check = checkCapacity(usage, bytes)
  if (!check.ok) return check
  const record = { addedAt: Date.now(), bytes, ...payload }
  await putBackground(record, factory)
  return { ok: true, record }
}
