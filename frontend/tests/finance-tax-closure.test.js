import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  canDelete,
  canEdit,
  canExportA200000,
  editFormFrom,
  editPayloadFrom,
  exportBlockedHint,
  toWan,
} from '../src/modules/finance-tax/returnActions'

const listSource = readFileSync(
  new URL('../src/modules/finance-tax/views/TaxReturnListView.vue', import.meta.url),
  'utf8',
)
const previewSource = readFileSync(
  new URL('../src/modules/finance-tax/views/TaxReturnPreviewView.vue', import.meta.url),
  'utf8',
)
const calcSource = readFileSync(
  new URL('../src/modules/finance-tax/views/TaxCalculateView.vue', import.meta.url),
  'utf8',
)
const enterpriseSource = readFileSync(
  new URL('../src/modules/finance-tax/views/EnterpriseFormView.vue', import.meta.url),
  'utf8',
)

describe('申报记录动作口径（纯函数）', () => {
  it('草稿可更正/删除，已提交受保护', () => {
    const draft = { status: 'draft', quarter: 1, return_type: '预缴' }
    const submitted = { status: 'submitted', quarter: 1, return_type: '预缴' }
    expect(canEdit(draft)).toBe(true)
    expect(canDelete(draft)).toBe(true)
    expect(canEdit(submitted)).toBe(false)
    expect(canDelete(submitted)).toBe(false)
    expect(canEdit(null)).toBe(false)
  })

  it('只有「预缴 + 有季度」能导出 A200000（年度/缺季度都不行）', () => {
    expect(canExportA200000({ quarter: 1, return_type: '预缴' })).toBe(true)
    expect(canExportA200000({ quarter: 4, return_type: '预缴' })).toBe(true)
    expect(canExportA200000({ quarter: null, return_type: '预缴' })).toBe(false)
    expect(canExportA200000({ quarter: 2, return_type: '年度' })).toBe(false)
    expect(canExportA200000({})).toBe(false)
  })

  it('导出禁用原因按记录类型区分：年度指 A100000，缺季度指更正补季度', () => {
    expect(exportBlockedHint({ return_type: '年度', quarter: null })).toContain('A100000')
    const legacy = exportBlockedHint({ return_type: '预缴', quarter: null })
    expect(legacy).toContain('更正')
    expect(legacy).toContain('季度')
    expect(legacy).not.toContain('A100000')
  })

  it('更正表单按元→万元回填，且带申报期与类型', () => {
    const form = editFormFrom({
      year: 2026, quarter: 2, return_type: '预缴',
      operating_revenue: 5_000_000, other_income: 0, operating_cost: 2_000_000,
      total_expenses: 1_000_000, tax_adjustments: 500_000, tax_preferential: 0,
    })
    expect(form.tax_period).toBe('2026')
    expect(form.quarter).toBe('Q2')
    expect(form.report_type).toBe('A类')
    expect(form.total_revenue).toBe(500)
    expect(form.other_income).toBe(0)
    expect(form.total_costs).toBe(200)
    expect(form.total_expenses).toBe(100)
    expect(form.tax_adjustments).toBe(50)
    expect(form.preferential_choice).toBe('')
  })

  it('元→万元保留分位：1234.56 元回填后仍精确还原 1234.56 元', () => {
    expect(toWan(1234.56)).toBe(0.123456)
    expect(toWan(1234.56) * 10000).toBeCloseTo(1234.56, 6)
    // 旧实现 toFixed(2) 会把 1234.56 元变成 0.12 万元 = 1200 元（丢 34.56 元）
    expect(toWan(5_000_000)).toBe(500)
  })

  it('更正提交体把年度转成字符串，并把年度类型归一为无季度', () => {
    const payload = editPayloadFrom({
      tax_period: 2026, quarter: 'Q2', report_type: 'A类',
      total_revenue: 1, other_income: 0, total_costs: 0, total_expenses: 0,
      tax_adjustments: 0, tax_preferential: 0, preferential_choice: '',
    }, 7)
    expect(payload.tax_period).toBe('2026')
    expect(payload.quarter).toBe('Q2')
    expect(payload.enterprise_id).toBe(7)

    const annual = editPayloadFrom({
      tax_period: 2026, quarter: 'Q2', report_type: 'B类',
      total_revenue: 1, other_income: 0, total_costs: 0, total_expenses: 0,
      tax_adjustments: 0, tax_preferential: 0, preferential_choice: '',
    }, 7)
    expect(annual.quarter).toBe(null)
    expect(annual.report_type).toBe('B类')
  })

  it('toWan 对空值/非法值返回 0', () => {
    expect(toWan(undefined)).toBe(0)
    expect(toWan(null)).toBe(0)
    expect(toWan('abc')).toBe(0)
    expect(toWan(123456)).toBe(12.3456)
  })
})

describe('申报记录视图契约', () => {
  it('列表页提供更正/删除入口并复用纯函数判定', () => {
    expect(listSource).toContain('canEdit(row)')
    expect(listSource).toContain('canDelete(row)')
    expect(listSource).toContain('canExportA200000(row)')
    expect(listSource).toContain('openEdit')
    expect(listSource).toContain('ElMessageBox.confirm')
    expect(listSource).toContain('/finance/tax/tax-returns/${row.id}')
  })

  it('年度记录的导出按钮禁用并给出原因', () => {
    expect(listSource).toContain(':disabled="!canExportA200000(row)"')
    expect(listSource).toContain('exportBlockedHint(row)')
    expect(listSource).toContain('A100000')
  })

  it('更正弹窗可改年度/季度/类型（历史缺季度补填路径）', () => {
    expect(listSource).toContain('v-model="editForm.tax_period"')
    expect(listSource).toContain('v-model="editForm.quarter"')
    expect(listSource).toContain('v-model="editForm.report_type"')
    expect(listSource).not.toContain(':precision="2"')
    expect(listSource).toContain('editPayloadFrom')
  })

  it('预览页不再请求恒空的调整明细接口', () => {
    expect(previewSource).not.toContain('/adjustments')
    expect(previewSource).toContain('operating_revenue')
    expect(previewSource).toContain('total_profit')
  })

  it('计算页高新只读展示企业档案，并提供优惠选择与设备折旧', () => {
    expect(calcSource).not.toContain('v-model="form.is_high_tech"')
    expect(calcSource).toContain('isHighTech')
    expect(calcSource).toContain('preferential_choice')
    expect(calcSource).toContain('equipment_depreciation')
    expect(calcSource).toContain("quarter: form.quarter")
    expect(calcSource).toContain('不得叠加')
  })

  it('计算页说明季报不纳入纳税调整（调整留待年度）', () => {
    expect(calcSource).toContain('不纳入')
    expect(calcSource).toContain('年度汇算')
    expect(calcSource).toContain('只留痕')
  })

  it('计算页并列展示「年度测算（含调整）」与「季度预缴（不含调整）」两个口径', () => {
    expect(calcSource).toContain('年度测算应纳税额(万，含调整)')
    expect(calcSource).toContain('季度预缴应纳税所得额(万，不含调整)')
    expect(calcSource).toContain('季度预缴应纳税额(万，与保存/导出一致)')
    expect(calcSource).toContain('result.prepay_tax_amount_wan')
    expect(calcSource).toContain('result.prepay_taxable_income_wan')
    expect(calcSource).toContain('result.prepay_note')
  })

  it('企业档案可维护高新技术资格', () => {
    expect(enterpriseSource).toContain('v-model="form.is_high_tech"')
    expect(enterpriseSource).toContain('高新技术企业')
  })
})
