// T6D / 独立复核 T3-I-3 交接项：四税种（增值税/个税/印花税/残保金）必须提供
// 受控更正入口调用 PUT /filings/{id}；已导出/已提交的 409 由 axios 拦截器统一提示，
// 视图不得重复 toast，也不得误报成功。
// 复审补项：M-3/N-4 印花税按月/按季 + 更正保留原季度；N-5 残保金回填社平工资；
// N-6 已提交/已导出禁用更正与删除。
import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))
const ElMessage = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }), onMounted: () => {} }
})
vi.mock('element-plus', () => ({
  ElMessage,
  ElMessageBox: { confirm: vi.fn().mockResolvedValue() },
}))
vi.mock('../src/api', () => ({ default: api, BACKEND_HINT: '测试后端提示' }))

const VatView = (await import('../src/modules/finance-vat/views/VatView.vue')).default
const IitView = (await import('../src/modules/finance-iit/views/IitView.vue')).default
const StampDutyView = (await import('../src/modules/finance-stamp-duty/views/StampDutyView.vue')).default
const DsfView = (await import('../src/modules/finance-dsf/views/DsfView.vue')).default

function srcOf(rel) {
  return readFileSync(new URL(`../src/modules/${rel}`, import.meta.url), 'utf8')
}

const CASES = [
  {
    name: '增值税',
    view: VatView,
    source: srcOf('finance-vat/views/VatView.vue'),
    url: (id) => `/finance/vat/filings/${id}`,
    row: {
      id: 5,
      period: '2026-10',
      taxpayer_type: 'general',
      taxpayer_name: '合成甲公司',
      status: 'calculated',
      has_export: false,
      calc_result: { output_vat: 130, input_vat: 50, input_transfer_out: 0, payable: 80 },
      inputs: {
        output_items: [{ business_type: '销售货物', amount: 1000, rate: 0.13 }],
        input_vat: 50,
        input_transfer_out: 0,
        sales_amount: 0,
        levy_rate: 0.01,
        filing_period: 'quarter',
      },
    },
    payload: { period: '2026-10', taxpayer_name: '合成甲公司', input_vat: 50 },
  },
  {
    name: '个税',
    view: IitView,
    source: srcOf('finance-iit/views/IitView.vue'),
    url: (id) => `/finance/iit/filings/${id}`,
    row: {
      id: 6,
      year: '2026',
      taxpayer_name: '合成乙公司',
      status: 'calculated',
      has_export: false,
      calc_result: { totals: { tax_withheld: 0 }, warnings: [] },
      rows: [{ month: 1, name: '张三', employee_id: '', employment_start_month: 1, salary: 10000, social_insurance: 1000, special_additional: 0, other_deductions: 0 }],
    },
    payload: { year: '2026', taxpayer_name: '合成乙公司' },
  },
  {
    name: '印花税',
    view: StampDutyView,
    source: srcOf('finance-stamp-duty/views/StampDutyView.vue'),
    url: (id) => `/finance/stamp-duty/filings/${id}`,
    row: {
      id: 7,
      period: '2026-10',
      taxpayer_name: '合成丙公司',
      status: 'calculated',
      has_export: false,
      calc_result: { total_tax: 100, raw_total: 200, half_saved: 100, filing_period: 'month' },
      half_discount: true,
      items: [{ tax_item: '买卖合同', amount: 1000000, exempt: false }],
    },
    payload: { period: '2026-10', taxpayer_name: '合成丙公司', half_discount: true },
  },
  {
    name: '残保金',
    view: DsfView,
    source: srcOf('finance-dsf/views/DsfView.vue'),
    url: (id) => `/finance/dsf/filings/${id}`,
    row: {
      id: 8,
      year: '2025',
      taxpayer_name: '合成丁公司',
      status: 'calculated',
      has_export: false,
      calc_result: { exempt: false, note: '合成口径', payable: 100 },
      inputs: { total_employees: 30, total_salary: 0, disabled_employees: 0, ratio: 0.015, local_avg_wage: null },
    },
    payload: { year: '2025', taxpayer_name: '合成丁公司', total_employees: 30 },
  },
]

function mount(view) {
  return view.setup({}, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe.each(CASES)('$name 更正入口（T6D）', ({ view, source, url, row, payload }) => {
  it('startRevise 回填原填报并把提交切到 PUT', async () => {
    const ctx = mount(view)
    ctx.startRevise(row)
    expect(ctx.revisingId.value).toBe(row.id)

    api.put.mockResolvedValue({ data: { ...row, status: 'calculated' } })
    await ctx.submit()

    expect(api.put).toHaveBeenCalledTimes(1)
    expect(api.put.mock.calls[0][0]).toBe(url(row.id))
    expect(api.put.mock.calls[0][1]).toMatchObject(payload)
    expect(api.post).not.toHaveBeenCalled()
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
  })

  it('已导出/已提交 409：交给拦截器提示，不重复 toast、不误报成功', async () => {
    const ctx = mount(view)
    ctx.startRevise(row)
    api.put.mockRejectedValue({ response: { status: 409, data: { detail: '该申报已导出底稿，不能直接覆盖更正' } } })
    await ctx.submit()

    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
    // 保留更正态便于按后端文案调整后重试
    expect(ctx.revisingId.value).toBe(row.id)
  })

  it('取消更正回到新建口径', () => {
    const ctx = mount(view)
    ctx.startRevise(row)
    ctx.cancelRevise()
    expect(ctx.revisingId.value).toBe(null)
  })

  it('视图提供更正按钮并调用 PUT 路由', () => {
    expect(source).toContain('startRevise(row)')
    expect(source).toContain('revisingId')
    expect(source).toContain('api.put(`/finance/')
  })

  it('已提交/已导出禁用更正；已提交禁用删除（N-6）', () => {
    const ctx = mount(view)
    expect(ctx.reviseBlocked({ status: 'calculated', has_export: false })).toBe(false)
    expect(ctx.reviseBlocked({ status: 'submitted', has_export: false })).toBe(true)
    expect(ctx.reviseBlocked({ status: 'calculated', has_export: true })).toBe(true)
    // 已提交不可删除；已导出底稿仍允许删除重填（后端同口径）
    expect(ctx.deleteBlocked({ status: 'submitted' })).toBe(true)
    expect(ctx.deleteBlocked({ status: 'calculated', has_export: true })).toBe(false)
  })

  it('模板按 reviseBlocked/deleteBlocked 禁用按钮', () => {
    expect(source).toContain(':disabled="reviseBlocked(row)"')
    expect(source).toContain(':disabled="deleteBlocked(row)"')
  })
})

describe('印花税申报周期（M-3 / N-4）', () => {
  const stamp = CASES.find((c) => c.name === '印花税')

  it('表单默认按月并提交 filing_period', async () => {
    const ctx = mount(StampDutyView)
    expect(ctx.form.value.filing_period).toBe('month')
    ctx.form.value.period = '2026-10'
    ctx.form.value.items = [{ tax_item: '买卖合同', amount: 1000, exempt: false }]
    api.post.mockResolvedValue({ data: { calc_result: { total_tax: 0.5, raw_total: 1, half_saved: 0.5, filing_period: 'month' } } })
    await ctx.submit()
    expect(api.post.mock.calls[0][1].filing_period).toBe('month')
  })

  it('更正保留原季度（不把 quarter 降级成 month）', async () => {
    const ctx = mount(StampDutyView)
    const quarterRow = {
      ...stamp.row,
      period: '2026-09',
      calc_result: { total_tax: 100, raw_total: 200, half_saved: 100, filing_period: 'quarter' },
    }
    ctx.startRevise(quarterRow)
    expect(ctx.form.value.filing_period).toBe('quarter')
    expect(ctx.cancelRevise).toBeTypeOf('function')
    api.put.mockResolvedValue({ data: { calc_result: { total_tax: 100, raw_total: 200, half_saved: 100, filing_period: 'quarter' } } })
    await ctx.submit()
    expect(api.put.mock.calls[0][1].filing_period).toBe('quarter')
  })

  it('视图提供按月/按季选择并在更正时锁定', () => {
    expect(stamp.source).toContain('v-model="form.filing_period"')
    expect(stamp.source).toContain('按季')
    expect(stamp.source).toContain(':disabled="Boolean(revisingId)"')
  })
})

describe('残保金社平工资回填（N-5）', () => {
  const dsf = CASES.find((c) => c.name === '残保金')

  it('更正从 inputs 回填 local_avg_wage 并随 PUT 提交', async () => {
    const ctx = mount(DsfView)
    const row = { ...dsf.row, inputs: { ...dsf.row.inputs, local_avg_wage: 120000 } }
    ctx.startRevise(row)
    expect(ctx.form.value.local_avg_wage).toBe(120000)
    api.put.mockResolvedValue({ data: { calc_result: { exempt: false, note: 'n', payable: 1 } } })
    await ctx.submit()
    expect(api.put.mock.calls[0][1].local_avg_wage).toBe(120000)
  })

  it('新建时未填社平工资提交 null（后端按未校验上限给 warning）', async () => {
    const ctx = mount(DsfView)
    ctx.form.value.year = '2025'
    api.post.mockResolvedValue({ data: { calc_result: { exempt: false, note: 'n', payable: 1 } } })
    await ctx.submit()
    expect(api.post.mock.calls[0][1].local_avg_wage).toBe(null)
  })

  it('视图提供社平工资输入（可空）', () => {
    expect(dsf.source).toContain('v-model="form.local_avg_wage"')
    expect(dsf.source).toContain('2 倍')
  })
})
