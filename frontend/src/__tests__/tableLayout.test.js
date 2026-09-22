import { describe, it, expect } from 'vitest'
import {
  HISTORY_COLUMN_BREAKPOINTS,
  MODELS_COLUMN_BREAKPOINTS,
  resolveTableLayout,
} from '../utils/tableLayout'

describe('resolveTableLayout', () => {
  it('按容器宽度分档：full / compact / narrow', () => {
    const bp = { full: 880, compact: 520 }
    expect(resolveTableLayout(1200, bp)).toBe('full')
    expect(resolveTableLayout(880, bp)).toBe('full')
    expect(resolveTableLayout(879, bp)).toBe('compact')
    expect(resolveTableLayout(520, bp)).toBe('compact')
    expect(resolveTableLayout(519, bp)).toBe('narrow')
    expect(resolveTableLayout(0, bp)).toBe('compact')
  })

  it('宽度未知（NaN / 非数字 / 负数）按 compact 兜底，不误判为 full', () => {
    const bp = { full: 880, compact: 520 }
    expect(resolveTableLayout(NaN, bp)).toBe('compact')
    expect(resolveTableLayout(undefined, bp)).toBe('compact')
    expect(resolveTableLayout(null, bp)).toBe('compact')
    expect(resolveTableLayout('abc', bp)).toBe('compact')
    expect(resolveTableLayout(-1, bp)).toBe('compact')
  })

  it('字符数字也能解析（clientWidth 来源是数字，防调用方传字符串）', () => {
    expect(resolveTableLayout('900', { full: 880, compact: 520 })).toBe('full')
  })

  it('两套断点常量的档位与实际列宽总和一致（窄档位最小，全列最大）', () => {
    expect(HISTORY_COLUMN_BREAKPOINTS.full).toBeGreaterThan(HISTORY_COLUMN_BREAKPOINTS.compact)
    expect(MODELS_COLUMN_BREAKPOINTS.full).toBeGreaterThan(MODELS_COLUMN_BREAKPOINTS.compact)
    // 历史页全列 40+220+80+84+120+150+176 = 870 < 890；紧凑列 40+220+84+176 = 520 < 540
    expect(HISTORY_COLUMN_BREAKPOINTS.full).toBeGreaterThanOrEqual(870)
    expect(HISTORY_COLUMN_BREAKPOINTS.compact).toBeGreaterThanOrEqual(520)
    // 模型页全列 200+240+130+60+130 = 760 < 780；紧凑列 200+240+130 = 570 < 590
    expect(MODELS_COLUMN_BREAKPOINTS.full).toBeGreaterThanOrEqual(760)
    expect(MODELS_COLUMN_BREAKPOINTS.compact).toBeGreaterThanOrEqual(570)
  })
})
