import { describe, expect, it } from 'vitest'
import { canConfirmImport, mappingFields } from '../src/modules/finance-ledger-bip/importForm'
import { categoryMatchesKind, formatPercent } from '../src/modules/finance-ledger-personal/utils'

// 探测发现的 bug 回归（dev/e2e/probes/finance-ledger.py 真实浏览器复现）：
// 1) BIP 导入向导：无法识别类型的文件，人工指定类型后前端不渲染列映射下拉，
//    且金额列无自动建议，确认入库必然 400（功能死路）；
// 2) 个人记账统计页：后端 percent 已是 0-100，前端又 ×100，75% 显示成 7500.0%；
// 3) 快速记账：选支出分类后切收入，旧分类残留导致提交被 400 拒绝。

describe('e2e-bug-finance-ledger：导入向导人工映射', () => {
  const unknownPreview = {
    file_type: 'unknown',
    required_fields: [],
    columns: ['甲', '乙', '丙', '丁'],
  }

  it('未知类型未指定类型时不可确认', () => {
    expect(canConfirmImport(unknownPreview, { file_type: '', period: '', mapping: {} })).toBe(false)
  })

  it('未知类型指定 voucher 后给出可映射字段清单（必填 + 金额列）', () => {
    const fields = mappingFields(unknownPreview, 'voucher')
    expect(fields).toEqual(expect.arrayContaining(['date', 'ledger', 'voucher_no', 'debit', 'credit']))
  })

  it('未知类型映射必填字段 + 一个金额列后才可确认', () => {
    const form = { file_type: 'voucher', period: '', mapping: { date: '甲', ledger: '乙', voucher_no: '丙' } }
    expect(canConfirmImport(unknownPreview, form)).toBe(false) // 缺金额列
    form.mapping.debit = '丁'
    expect(canConfirmImport(unknownPreview, form)).toBe(true)
  })

  it('未知类型指定 voucher 但缺必填字段不可确认', () => {
    expect(canConfirmImport(unknownPreview, { file_type: 'voucher', period: '', mapping: { date: '甲' } })).toBe(false)
  })

  it('未知类型指定 balance 时必须填合法期间', () => {
    const form = { file_type: 'balance', period: '', mapping: { subject_code: '甲', subject_name: '乙' } }
    expect(canConfirmImport(unknownPreview, form)).toBe(false)
    form.period = '2026-13'
    expect(canConfirmImport(unknownPreview, form)).toBe(false)
    form.period = '2026-10'
    expect(canConfirmImport(unknownPreview, form)).toBe(true)
  })

  it('自动识别类型的文件仍按预览 required_fields 渲染与校验', () => {
    const preview = { file_type: 'voucher', required_fields: ['date', 'ledger', 'voucher_no'] }
    expect(mappingFields(preview, 'voucher')).toEqual(['date', 'ledger', 'voucher_no'])
    expect(
      canConfirmImport(preview, { file_type: 'voucher', period: '', mapping: { date: '日期', ledger: '账簿', voucher_no: '凭证号' } })
    ).toBe(true)
    expect(canConfirmImport(preview, { file_type: 'voucher', period: '', mapping: {} })).toBe(false)
  })
})

describe('e2e-bug-finance-ledger：统计占比口径', () => {
  it('formatPercent 直接格式化 0-100 百分数（不再双重 ×100）', () => {
    expect(formatPercent(75)).toBe('75.0')
    expect(formatPercent(100)).toBe('100.0')
    expect(formatPercent(0)).toBe('0.0')
    expect(formatPercent(12.34)).toBe('12.3')
    expect(formatPercent(undefined)).toBe('0.0')
    expect(formatPercent(null)).toBe('0.0')
  })
})

describe('e2e-bug-finance-ledger：类型切换分类联动', () => {
  it('categoryMatchesKind 识别分类与交易类型是否一致', () => {
    expect(categoryMatchesKind({ kind: 'expense' }, 'expense')).toBe(true)
    expect(categoryMatchesKind({ kind: 'income' }, 'expense')).toBe(false)
    expect(categoryMatchesKind(null, 'expense')).toBe(false)
    expect(categoryMatchesKind(undefined, 'income')).toBe(false)
  })
})
