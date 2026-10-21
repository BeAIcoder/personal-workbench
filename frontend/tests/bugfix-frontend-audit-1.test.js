// 前端行为缺陷回归测试（bug 挖掘批次：frontend-audit-2026-10-20，第一批）
// 纪律：每个 it 对应一个经源码/后端契约交叉验证的真实缺陷或既有不变量。
// 断言行为与纯函数输出，不咬死组件源码字符串（那种断言改个写法就假红）。
import { describe, expect, it } from 'vitest'
import { currentMonth, monthOptions } from '../src/modules/finance-cashflow/month'
import { formatPercent, categoryMatchesKind } from '../src/modules/finance-ledger-personal/utils'
import { isRedInvoice } from '../src/modules/finance-invoice/itemDisplay'
import { toTableRows } from '../src/modules/finance-vault/tableRows'
import { canConfirmImport } from '../src/modules/finance-ledger-bip/importForm'

describe('BUG-1 现金流回填月份 off-by-one（真实缺陷：1 月提交 0 被 422，其余月偏小一月）', () => {
  it('currentMonth 返回 1-12，与后端 Form(ge=1, le=12) 对齐', () => {
    expect(currentMonth(new Date(2026, 0, 15))).toBe(1) // 1 月：旧实现给 0
    expect(currentMonth(new Date(2026, 1, 15))).toBe(2)
    expect(currentMonth(new Date(2026, 11, 31))).toBe(12)
  })

  it('任意月份都不得越界到 0 或 13', () => {
    for (let m = 0; m < 12; m++) {
      const v = currentMonth(new Date(2026, m, 10))
      expect(v).toBeGreaterThanOrEqual(1)
      expect(v).toBeLessThanOrEqual(12)
      expect(v).toBe(m + 1)
    }
  })

  it('下拉选项为 1-12 且默认值在其中', () => {
    const opts = monthOptions()
    expect(opts).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    for (let m = 0; m < 12; m++) expect(opts).toContain(currentMonth(new Date(2026, m, 10)))
  })
})

describe('BUG-2 个人记账统计占比（既有不变量防回归：percent 已是 0-100，不得再 ×100）', () => {
  it('formatPercent 直接格式化百分数', () => {
    expect(formatPercent(75)).toBe('75.0')
    expect(formatPercent(0)).toBe('0.0')
    expect(formatPercent(null)).toBe('0.0')
    expect(formatPercent(undefined)).toBe('0.0')
    expect(formatPercent(12.34)).toBe('12.3')
  })
})

describe('BUG-3 红字发票判定（既有不变量：冲红发票/红字两种引擎口径都要认）', () => {
  it('isRedInvoice 兼容两种 direction 文案', () => {
    expect(isRedInvoice({ invoice_direction: '冲红发票' })).toBe(true)
    expect(isRedInvoice({ invoice_direction: '红字' })).toBe(true)
    expect(isRedInvoice({ invoice_direction: '正常发票' })).toBe(false)
    expect(isRedInvoice(null)).toBe(false)
  })
})

describe('BUG-4 档案库查询行扁平化（既有不变量：列名含点号/行是数组时仍能取值）', () => {
  it('列名带点号也按列序映射到 c0/c1', () => {
    const cols = ['a.b', 'c']
    const rows = toTableRows(cols, [[1, 2], [3, 4]])
    expect(rows).toEqual([
      { c0: 1, c1: 2 },
      { c0: 3, c1: 4 },
    ])
  })

  it('空列/空行安全返回', () => {
    expect(toTableRows([], [])).toEqual([])
    expect(toTableRows(['x'], [])).toEqual([])
  })
})

describe('BUG-5 BIP 导入人工指定类型的确认条件（既有不变量：金额列至少映射一个）', () => {
  const preview = { file_type: 'unknown' }
  const mapping = { date: 'A', ledger: 'B', voucher_no: 'C', debit: 'D' }

  it('未指定类型时不可确认', () => {
    expect(canConfirmImport(preview, { mapping: {} })).toBe(false)
  })

  it('指定类型且金额列已映射时可确认', () => {
    expect(canConfirmImport(preview, { file_type: 'voucher', mapping, period: '' })).toBe(true)
  })

  it('balance 类型缺少合法期间不可确认', () => {
    expect(canConfirmImport(preview, { file_type: 'balance', mapping: { subject_code: 'A', subject_name: 'B' }, period: '2026-13' })).toBe(false)
    expect(canConfirmImport(preview, { file_type: 'balance', mapping: { subject_code: 'A', subject_name: 'B' }, period: '2026-01' })).toBe(true)
  })
})

describe('BUG-6 分类与交易类型一致性（既有不变量）', () => {
  it('categoryMatchesKind 比较 kind', () => {
    expect(categoryMatchesKind({ kind: 'expense' }, 'expense')).toBe(true)
    expect(categoryMatchesKind({ kind: 'income' }, 'expense')).toBe(false)
    expect(categoryMatchesKind(null, 'expense')).toBe(false)
  })
})
