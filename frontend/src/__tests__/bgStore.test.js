// @vitest-environment node
// ↑ 本用例用 fake-indexeddb 验证 Blob 存取。jsdom 的 Blob 与 structuredClone 不兼容
//   （克隆后不再是 Blob 实例），切到 node 环境用原生 Blob / structuredClone。
import { describe, it, expect, beforeEach } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import {
  openDb,
  isAvailable,
  listBackgrounds,
  getBackground,
  putBackground,
  deleteBackground,
  libraryUsage,
  checkCapacity,
  addBackground,
  LIBRARY_LIMITS,
} from '../utils/bgStore'

/** 每个用例一个独立的 IndexedDB 实例（互不污染）。 */
let idb
beforeEach(() => {
  idb = new IDBFactory()
})

/** 造一条记录。 */
function rec(id, extra = {}) {
  return {
    id,
    name: `背景 ${id}`,
    blob: new Blob([new Uint8Array(64)], { type: 'image/jpeg' }),
    thumbUrl: 'data:image/jpeg;base64,AAAA',
    width: 1600,
    height: 900,
    bytes: 64,
    addedAt: 1000,
    ...extra,
  }
}

describe('openDb / isAvailable', () => {
  it('能创建并打开数据库', async () => {
    const db = await openDb(idb)
    expect(db).toBeTruthy()
    expect(db.objectStoreNames.contains('backgrounds')).toBe(true)
    db.close()
  })

  it('环境不支持 IndexedDB 时可读原因 reject（不静默）', async () => {
    await expect(openDb(null)).rejects.toThrow('不支持 IndexedDB')
  })

  it('isAvailable：可用返回 true；不可用返回 false 而不抛错', async () => {
    await expect(isAvailable(idb)).resolves.toBe(true)
    await expect(isAvailable(null)).resolves.toBe(false)
  })
})

describe('put / get / list / delete', () => {
  it('写入后能按 id 取回，blob 保持为 Blob 且大小一致', async () => {
    await putBackground(rec('a'), idb)
    const got = await getBackground('a', idb)
    expect(got.id).toBe('a')
    expect(got.blob).toBeInstanceOf(Blob)
    expect(got.blob.size).toBe(64)
    expect(got.thumbUrl).toContain('data:image/jpeg')
  })

  it('取不存在的 id 返回 null（不抛错）', async () => {
    await expect(getBackground('nope', idb)).resolves.toBeNull()
  })

  it('list 返回全部记录的**元数据**，且**不含 blob**（避免把原图读进内存）', async () => {
    await putBackground(rec('a'), idb)
    await putBackground(rec('b'), idb)
    const list = await listBackgrounds(idb)
    expect(list).toHaveLength(2)
    for (const item of list) {
      expect(item).not.toHaveProperty('blob')
      expect(item.thumbUrl).toBeTruthy()
      expect(item.id).toBeTruthy()
    }
  })

  it('list 按 addedAt 倒序（新上传的排前面）', async () => {
    await putBackground(rec('old', { addedAt: 100 }), idb)
    await putBackground(rec('new', { addedAt: 900 }), idb)
    await putBackground(rec('mid', { addedAt: 500 }), idb)
    const ids = (await listBackgrounds(idb)).map((r) => r.id)
    expect(ids).toEqual(['new', 'mid', 'old'])
  })

  it('同 id 再写入是覆盖，不是新增', async () => {
    await putBackground(rec('a', { name: '旧' }), idb)
    await putBackground(rec('a', { name: '新' }), idb)
    const list = await listBackgrounds(idb)
    expect(list).toHaveLength(1)
    expect(list[0].name).toBe('新')
  })

  it('删除后取不到，且删除不存在的 id 是幂等的', async () => {
    await putBackground(rec('a'), idb)
    await deleteBackground('a', idb)
    await expect(getBackground('a', idb)).resolves.toBeNull()
    await expect(deleteBackground('a', idb)).resolves.toBe(true)
  })

  it('空库 list 返回空数组而不是 null', async () => {
    await expect(listBackgrounds(idb)).resolves.toEqual([])
  })
})

describe('libraryUsage', () => {
  it('统计张数与总字节', async () => {
    await putBackground(rec('a', { bytes: 100 }), idb)
    await putBackground(rec('b', { bytes: 250 }), idb)
    expect(await libraryUsage(idb)).toEqual({ count: 2, bytes: 350 })
  })

  it('bytes 缺失时用 blob.size 兜底', async () => {
    const r = rec('a')
    delete r.bytes
    await putBackground(r, idb)
    expect((await libraryUsage(idb)).bytes).toBe(64)
  })

  it('空库为 0/0', async () => {
    expect(await libraryUsage(idb)).toEqual({ count: 0, bytes: 0 })
  })
})

describe('checkCapacity（纯函数）', () => {
  it('未超限时通过', () => {
    expect(checkCapacity({ count: 1, bytes: 100 }, 100).ok).toBe(true)
  })

  it('张数超限：给出可行动提示（含上限值）', () => {
    const res = checkCapacity({ count: LIBRARY_LIMITS.maxCount, bytes: 0 }, 10)
    expect(res.ok).toBe(false)
    expect(res.error).toContain(String(LIBRARY_LIMITS.maxCount))
    expect(res.error).toContain('删除')
  })

  it('张数恰好到上限前一张时仍可通过（边界）', () => {
    expect(checkCapacity({ count: LIBRARY_LIMITS.maxCount - 1, bytes: 0 }, 10).ok).toBe(true)
  })

  it('字节超限：提示含 MB 上限', () => {
    const res = checkCapacity({ count: 0, bytes: LIBRARY_LIMITS.maxBytes }, 1)
    expect(res.ok).toBe(false)
    expect(res.error).toContain('MB')
  })

  it('字节恰好用满但新增为 0 时通过（边界）', () => {
    expect(checkCapacity({ count: 0, bytes: LIBRARY_LIMITS.maxBytes }, 0).ok).toBe(true)
  })

  it('usage 缺失或非法按 0 处理', () => {
    expect(checkCapacity(null, 10).ok).toBe(true)
    expect(checkCapacity(undefined, NaN).ok).toBe(true)
  })

  it('自定义上限生效', () => {
    expect(checkCapacity({ count: 1, bytes: 0 }, 0, { maxCount: 1, maxBytes: 100 }).ok).toBe(false)
  })
})

describe('addBackground（容量校验 + 写入）', () => {
  it('正常写入并补齐 addedAt / bytes', async () => {
    const res = await addBackground({ id: 'x', name: 'X', blob: new Blob([new Uint8Array(32)]) }, idb)
    expect(res.ok).toBe(true)
    expect(res.record.addedAt).toBeGreaterThan(0)
    expect(res.record.bytes).toBe(32)
    expect((await listBackgrounds(idb))).toHaveLength(1)
  })

  it('超限时拒绝写入且**不落库**（错误原因可行动）', async () => {
    const res = await addBackground(
      { id: 'x', name: 'X', blob: new Blob([new Uint8Array(32)]) },
      idb,
    )
    expect(res.ok).toBe(true)
    // 人为把库塞满到上限
    for (let i = 0; i < LIBRARY_LIMITS.maxCount - 1; i++) {
      await putBackground(rec(`fill-${i}`), idb)
    }
    const full = await addBackground({ id: 'y', name: 'Y', blob: new Blob([new Uint8Array(8)]) }, idb)
    expect(full.ok).toBe(false)
    expect(full.error).toContain('删除')
    expect(await getBackground('y', idb)).toBeNull()
  })
})
