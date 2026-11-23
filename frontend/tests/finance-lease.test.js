// 租赁核算前端：合同草稿编辑、付款计划整批维护、xlsx 预览与确认导入、测算与期间表、
// 三态校验（通过/差异/未知）、过期提示、确认 409、允许历史快照导出。
// node 环境无 DOM：沿用 finance-recon 惯例，mock 生命周期、消息组件、api 与下载实现，
// 直接驱动编译后的 setup()，只验证行为（状态与请求），不渲染模板。
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))
const saveBlob = vi.hoisted(() => vi.fn(() => true))
const mountedCbs = vi.hoisted(() => [])

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    // node 环境无真实挂载：捕获 onMounted 回调，测试手动触发以验证「挂载即请求」
    onMounted: (cb) => mountedCbs.push(cb),
    onUnmounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))
vi.mock('../src/api', () => ({ default: api, API_BASE: '/api' }))
vi.mock('../src/utils/download.js', () => ({
  saveBlob,
  openPrintWindow: vi.fn(),
  showPdfInWindow: vi.fn(),
  REVOKE_DELAY_MS: 10000,
  PRINT_REVOKE_DELAY_MS: 600000,
}))

const manifest = (await import('../src/modules/finance-lease/manifest.js')).default
const apiModule = await import('../src/modules/finance-lease/api.js')
const LeaseView = (await import('../src/modules/finance-lease/views/LeaseView.vue')).default
const ContractPanel = (await import('../src/modules/finance-lease/views/ContractPanel.vue')).default
const MeasurePanel = (await import('../src/modules/finance-lease/views/MeasurePanel.vue')).default
const CheckExportPanel = (await import('../src/modules/finance-lease/views/CheckExportPanel.vue')).default
const { ElMessage, ElMessageBox } = await import('element-plus')
const {
  leaseApi,
  buildContractPayload,
  buildPaymentsPayload,
  checkStateMeta,
  calculationStatusMeta,
  contractStatusMeta,
  normalizeChecks,
  normalizeCounts,
  confirmBlockReason,
  needsAllowStale,
  exportWarning,
  exportFileName,
  exportFileNameFromResponse,
  previewZeroRows,
  paymentsToEditor,
  renumberPayments,
} = apiModule

function mountPanel(Component, props = {}) {
  const events = []
  const ctx = Component.setup(props, { expose: () => {}, emit: (name, payload) => events.push({ name, payload }) })
  return { ctx, events }
}
const mounted = (Component, props) => mountPanel(Component, props).ctx

async function fireMounted() {
  mountedCbs.splice(0).forEach((cb) => cb())
  await new Promise((resolve) => setTimeout(resolve, 0))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

function routeGet(map) {
  api.get.mockImplementation((url) => {
    if (Object.prototype.hasOwnProperty.call(map, url)) return Promise.resolve({ data: map[url] })
    return Promise.reject(new Error(`未预期的 GET ${url}`))
  })
}

const CONTRACT = {
  id: 5, contract_no: 'SYN-LEASE-001', name: '示例场地租赁', lessee: '示例承租人',
  project: '示例项目', lessor: '示例出租方', start_date: '2026-01-01', end_date: '2029-12-31',
  annual_rate: '0.035000', rate_basis: '增量借款利率', remark: '', attachment_note: '',
  status: 'draft', status_label: '草稿', payment_count: 2, calculation_count: 1,
  confirmed_calculation_count: 0, latest_calculation_id: 11,
  latest_calculation_status: 'calculated', latest_calculation_stale: false,
  created_at: '2026-11-18T10:00:00', updated_at: '2026-11-18T10:00:00',
}
const CONTRACT_PAYLOAD = {
  contract_no: 'SYN-LEASE-001', name: '示例场地租赁', lessee: '示例承租人', project: '示例项目',
  lessor: '示例出租方', start_date: '2026-01-01', end_date: '2029-12-31',
  annual_rate: '0.035000', rate_basis: '增量借款利率', remark: '', attachment_note: '',
}
const CALC = {
  id: 11, contract_id: 5, status: 'calculated', status_label: '已测算', input_hash: 'a'.repeat(64),
  rule_version: 'lease-v1',
  summary: { total_payment: '14691.34', present_value: '12345.67', total_interest: '2345.67',
    total_depreciation: '12345.67', payment_count: 2, month_count: 36,
    first_period: '2026-01', last_period: '2028-12' },
  checks: [{ key: 'liability_rollforward', title: '负债滚动', status: 'ok', message: '期初+利息-付款=期末' }],
  check_counts: { ok: 3, failed: 0, unknown: 0 }, all_checks_passed: true, has_unknown_checks: false,
  stale: false, confirmed_at: null, created_at: '2026-11-18T10:00:00', updated_at: '2026-11-18T10:00:00',
}
const ASSESS_ROW = {
  sequence: 1, source_row: 4, period_start: '2026-01-01', period_end: '2026-12-31', months: 12,
  amount: '5000.00', period_rate: '0.002917', discount_factor: '0.965456123', present_value: '4827.28',
  unamortized_finance: '172.72', interest_u: '168.00', liability_balance: '0.00',
}
const PERIOD_ROW = {
  period_month: '2026-01', sequence: 1, depreciation: '342.93', payable: '416.67',
  interest: '14.00', lease_payment: '0.00', unamortized_finance: '158.72', liability_balance: '4658.28',
}
const PAYMENTS = [
  { id: 1, contract_id: 5, sequence: 1, source_row: 4, period_start: '2026-01-01', period_end: '2026-12-31', months: 12, amount: '5000.00', start_flag: true, prepay_flag: true, is_free_rent: false, note: '首期期初支付' },
  { id: 2, contract_id: 5, sequence: 2, source_row: 5, period_start: '2027-01-01', period_end: '2027-12-31', months: 0, amount: '0.00', start_flag: false, prepay_flag: false, is_free_rent: true, note: '免租期' },
]
const PREVIEW = {
  sheet_name: '付款计划', header_map: { 期次: 'sequence' },
  items: [
    { sequence: 1, source_row: 4, period_start: '2026-01-01', period_end: '2026-12-31', months: 12, amount: '5000.00', start_flag: true, prepay_flag: true, is_free_rent: false, note: '' },
    { sequence: 2, source_row: 5, period_start: '2027-01-01', period_end: '2027-12-31', months: 0, amount: '0.00', start_flag: false, prepay_flag: false, is_free_rent: true, note: '免租期' },
  ],
  warnings: ['第 5 行：月数为 0，按末期 0 月期原样保留'], errors: [],
}
const CHECKS_OUT = {
  calculation_id: 11, input_hash: 'a'.repeat(64), rule_version: 'lease-v1', stale: false,
  checks: [
    { key: 'liability_rollforward', title: '负债滚动', status: 'ok', expected: '0.00', actual: '0.00', difference: '0.00', message: '期初+利息-付款=期末' },
    { key: 'remaining_payment', title: '剩余付款', status: 'ok', expected: '14691.34', actual: '14691.34', difference: '0.00', message: '付款计划与评估表一致' },
    { key: 'monthly_split', title: '月折旧/月利息', status: 'ok', expected: '12345.67', actual: '12345.67', difference: '0.00', message: '合计一致' },
  ],
  check_counts: { ok: 3, failed: 0, unknown: 0 }, all_passed: true, has_unknown: false,
}

beforeAll(() => {
  if (!URL.createObjectURL) URL.createObjectURL = () => 'blob:stub'
  if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {}
})

beforeEach(() => {
  vi.resetAllMocks()
  api.defaults = { baseURL: '/api' }
  mountedCbs.length = 0
})

describe('模块注册与 API 约定', () => {
  it('路由 /finance/lease 指向 LeaseView，菜单在财务/账务处理 order 98', () => {
    expect(manifest.name).toBe('finance-lease')
    expect(manifest.routes).toHaveLength(1)
    expect(manifest.routes[0]).toMatchObject({ path: '/finance/lease', name: 'finance-lease' })
    expect(typeof manifest.routes[0].component).toBe('function')
    expect(manifest.menu).toMatchObject({
      group: '财务', subgroup: '账务处理', title: '租赁核算', path: '/finance/lease', order: 98, icon: 'Document',
    })
  })

  it('菜单图标在 main.js 字符串图标注册表中（<component :is> 才能渲染）', () => {
    const source = readFileSync(fileURLToPath(new URL('../src/main.js', import.meta.url)), 'utf8')
    const start = source.indexOf('const STRING_ICONS = {')
    expect(start).toBeGreaterThan(-1)
    expect(source.slice(start, source.indexOf('}', start))).toContain(manifest.menu.icon)
  })

  it('leaseApi 路径不带 /api 前缀；导出默认不带 allow_stale', async () => {
    api.get.mockResolvedValue({ data: { items: [] } })
    api.post.mockResolvedValue({ data: {} })
    await leaseApi.contracts({ page: 1, page_size: 200 })
    await leaseApi.contract(5)
    await leaseApi.payments(5)
    await leaseApi.putPayments(5, { items: [] })
    await leaseApi.patchContract(5, {})
    await leaseApi.deleteContract(5)
    await leaseApi.previewImport(5, new FormData())
    await leaseApi.applyImport(5, new FormData())
    await leaseApi.calculations(5)
    await leaseApi.createCalculation(5)
    await leaseApi.calculation(11)
    await leaseApi.assessment(11)
    await leaseApi.periods(11)
    await leaseApi.checks(11)
    await leaseApi.confirm(11)
    await leaseApi.exportXlsx(11)
    await leaseApi.exportXlsx(11, { allowStale: true })
    await leaseApi.summary()
    expect(api.get.mock.calls.map((c) => c[0])).toEqual([
      '/finance/lease/contracts',
      '/finance/lease/contracts/5',
      '/finance/lease/contracts/5/payments',
      '/finance/lease/contracts/5/calculations',
      '/finance/lease/calculations/11',
      '/finance/lease/calculations/11/assessment',
      '/finance/lease/calculations/11/periods',
      '/finance/lease/calculations/11/checks',
      '/finance/lease/calculations/11/export.xlsx',
      '/finance/lease/calculations/11/export.xlsx',
      '/finance/lease/summary',
    ])
    expect(api.get).toHaveBeenCalledWith('/finance/lease/calculations/11/export.xlsx', {
      params: { allow_stale: true }, responseType: 'blob', timeout: 120000,
    })
    expect(api.post).toHaveBeenCalledWith('/finance/lease/contracts/5/payments/import/preview', expect.any(FormData), {
      headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000,
    })
    expect(api.post).toHaveBeenCalledWith('/finance/lease/contracts/5/payments/import/apply', expect.any(FormData), {
      headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000,
    })
    expect(api.post).toHaveBeenCalledWith('/finance/lease/contracts/5/calculations', null, { timeout: 120000 })
    expect(api.post).toHaveBeenCalledWith('/finance/lease/calculations/11/confirm')
  })
})

describe('状态映射：未知/差异/过期一律非绿色', () => {
  it('校验结论 ok 绿、failed 红、unknown 警告、未知枚举非绿', () => {
    expect(checkStateMeta('ok')).toMatchObject({ label: '通过', type: 'success' })
    expect(checkStateMeta('failed')).toMatchObject({ label: '差异', type: 'danger' })
    expect(checkStateMeta('unknown')).toMatchObject({ label: '未知', type: 'warning' })
    expect(checkStateMeta('weird').type).toBe('warning')
  })

  it('合同/测算状态与扁平 latest_* 字段标签', () => {
    expect(contractStatusMeta('draft').label).toBe('草稿')
    expect(contractStatusMeta('archived').label).toBe('已归档')
    expect(contractStatusMeta('weird').type).toBe('warning')
    expect(calculationStatusMeta('calculated').type).toBe('info')
    expect(calculationStatusMeta('confirmed').type).toBe('success')
    expect(calculationStatusMeta('weird').type).toBe('warning')
    expect(normalizeCounts(CONTRACT)).toEqual({ ok: 0, failed: 0, unknown: 0 })
  })

  it('空校验集合是未知而不是通过；计数可从 checks 现算', () => {
    expect(normalizeChecks({ checks: [] }).state).toBe('unknown')
    const derived = normalizeChecks({ checks: [{ key: 'a', title: 'A', status: 'ok' }, { key: 'b', title: 'B', status: 'failed' }] })
    expect(derived.counts).toEqual({ ok: 1, failed: 1, unknown: 0 })
    expect(derived.state).toBe('failed')
    const unknown = normalizeChecks({
      checks: [{ key: 'x', title: '剩余付款', status: 'unknown', message: '缺付款计划' }],
    })
    expect(unknown.state).toBe('unknown')
    expect(unknown.unknownChecks[0].message).toContain('缺付款计划')
  })

  it('确认门禁：已确认/过期/差异/未知/无结论都给出中文原因', () => {
    expect(confirmBlockReason(CALC)).toBe('')
    expect(confirmBlockReason({ ...CALC, stale: true })).toContain('已过期')
    expect(confirmBlockReason({ ...CALC, status: 'confirmed' })).toContain('不可改写')
    expect(confirmBlockReason({ ...CALC, status: 'weird' })).toContain('不可确认')
    expect(confirmBlockReason(CALC, { ok: 1, failed: 2, unknown: 0 })).toContain('2 项差异')
    expect(confirmBlockReason(CALC, { ok: 1, failed: 0, unknown: 1 })).toContain('未知')
    expect(confirmBlockReason(CALC, { ok: 0, failed: 0, unknown: 0 })).toContain('没有可确认')
    expect(confirmBlockReason(null)).toContain('请先选择')
  })

  it('导出警示与 allow_stale 判定：全部通过时两者皆空/假', () => {
    expect(needsAllowStale(CALC)).toBe(false)
    expect(exportWarning(CALC)).toBe('')
    expect(needsAllowStale({ ...CALC, stale: true })).toBe(true)
    expect(exportWarning({ ...CALC, stale: true })).toContain('已过期')
    expect(needsAllowStale(CALC, { ok: 1, failed: 1, unknown: 0 })).toBe(true)
    expect(exportWarning(CALC, { ok: 1, failed: 1, unknown: 0 })).toContain('差异')
    expect(needsAllowStale({ ...CALC, all_checks_passed: false })).toBe(true)
    expect(exportWarning({ ...CALC, all_checks_passed: false })).toContain('校验未全部通过')
  })

  it('导出文件名：后端 Content-Disposition 优先，回退合同号+测算日期', () => {
    expect(exportFileName(CONTRACT, CALC)).toBe('SYN-LEASE-001_租赁核算_2026-11-18.xlsx')
    const response = {
      headers: { 'content-disposition': "attachment; filename=\"lease.xlsx\"; filename*=utf-8''%E7%A7%9F%E8%B5%81%E5%BA%95%E7%A8%BF.xlsx" },
    }
    expect(exportFileNameFromResponse(response, CONTRACT, CALC)).toBe('租赁底稿.xlsx')
    expect(exportFileNameFromResponse({}, { contract_no: 'A/B:C' }, CALC)).not.toMatch(/[/\\:]/)
  })
})
describe('固定 schema 校验（不发送半成品请求）', () => {
  const base = { ...CONTRACT_PAYLOAD, annual_rate: 0.035 }

  it('合同写入体：比率 6 位小数（Numeric(9,6)）、remark 不是 note、空白裁剪', () => {
    expect(buildContractPayload({ ...base, name: '  示例场地租赁  ' })).toEqual(CONTRACT_PAYLOAD)
  })

  it('合同必填与日期/利率边界抛中文错误', () => {
    expect(() => buildContractPayload({ ...base, contract_no: ' ' })).toThrow('合同号不能为空')
    expect(() => buildContractPayload({ ...base, lessee: '' })).toThrow('承租人主体不能为空')
    expect(() => buildContractPayload({ ...base, end_date: '2025-12-31' })).toThrow('早于租赁起始日')
    expect(() => buildContractPayload({ ...base, start_date: '2026-1-1' })).toThrow('YYYY-MM-DD')
    expect(() => buildContractPayload({ ...base, annual_rate: null })).toThrow('年折现率不能为空')
    expect(() => buildContractPayload({ ...base, annual_rate: 1.2 })).toThrow('0~1')
    expect(() => buildContractPayload({ ...base, annual_rate: 0 })).toThrow('0~1')
  })

  it('付款计划：0 元免租期与 0 月期保留，期次 1..N 连续，日期/金额/月数校验', () => {
    const payload = buildPaymentsPayload([
      { sequence: 2, source_row: 5, period_start: '2027-01-01', period_end: '2027-12-31', months: 0, amount: 0, start_flag: false, prepay_flag: false, is_free_rent: true, note: '免租期' },
      { sequence: 1, source_row: 4, period_start: '2026-01-01', period_end: '2026-12-31', months: 12, amount: '5000', start_flag: true, prepay_flag: true, is_free_rent: false, note: '' },
    ])
    expect(payload.items).toEqual([
      { sequence: 1, source_row: 4, period_start: '2026-01-01', period_end: '2026-12-31', months: 12, amount: '5000.00', start_flag: true, prepay_flag: true, is_free_rent: false, note: '' },
      { sequence: 2, source_row: 5, period_start: '2027-01-01', period_end: '2027-12-31', months: 0, amount: '0.00', start_flag: false, prepay_flag: false, is_free_rent: true, note: '免租期' },
    ])
    expect(buildPaymentsPayload([])).toEqual({ items: [] })
    const row = { sequence: 1, period_start: '2026-01-01', period_end: '2026-12-31', months: 12, amount: 1 }
    expect(() => buildPaymentsPayload([row, { ...row, sequence: 1 }])).toThrow('第 1 期')
    expect(() => buildPaymentsPayload([{ ...row, sequence: 2 }])).toThrow('缺少第 1 期')
    expect(() => buildPaymentsPayload([{ ...row, period_end: '2025-12-31' }])).toThrow('早于开始日')
    expect(() => buildPaymentsPayload([{ ...row, period_start: '2026/01/01' }])).toThrow('YYYY-MM-DD')
    expect(() => buildPaymentsPayload([{ ...row, amount: null }])).toThrow('付款金额不能为空')
    expect(() => buildPaymentsPayload([{ ...row, months: -1 }])).toThrow('覆盖月数')
  })

  it('付款计划读取/重排：金额转数值、保留源行号与三个标志', () => {
    expect(paymentsToEditor(PAYMENTS)).toEqual([
      { sequence: 1, source_row: 4, period_start: '2026-01-01', period_end: '2026-12-31', months: 12, amount: 5000, start_flag: true, prepay_flag: true, is_free_rent: false, note: '首期期初支付' },
      { sequence: 2, source_row: 5, period_start: '2027-01-01', period_end: '2027-12-31', months: 0, amount: 0, start_flag: false, prepay_flag: false, is_free_rent: true, note: '免租期' },
    ])
    const rows = [{ sequence: 3 }, { sequence: 7 }]
    renumberPayments(rows)
    expect(rows.map((r) => r.sequence)).toEqual([1, 2])
  })

  it('预览的免租期/0 月期行数统计（用于确认前提示）', () => {
    expect(previewZeroRows({ items: PREVIEW.items })).toBe(1)
    expect(previewZeroRows({ items: [{ amount: '1.00', months: 12 }] })).toBe(0)
  })
})

describe('LeaseView：合同台账、创建与删除', () => {
  it('loadAll 同时拉取合同与摘要（page/page_size）；挂载即请求', async () => {
    routeGet({
      '/finance/lease/contracts': { total: 1, items: [CONTRACT] },
      '/finance/lease/summary': { total_contracts: 1, draft_count: 1, contracts_with_pending: 2 },
    })
    const ctx = mounted(LeaseView)
    expect(mountedCbs).toHaveLength(1)
    await fireMounted()
    expect(api.get).toHaveBeenCalledWith('/finance/lease/contracts', { params: { page: 1, page_size: 200 } })
    expect(api.get).toHaveBeenCalledWith('/finance/lease/summary', { params: {} })
    expect(ctx.contracts.value).toEqual([CONTRACT])
    expect(ctx.summary.value.contracts_with_pending).toBe(2)
    expect(ctx.contractsLoading.value).toBe(false)
  })

  it('新建合同：POST 固定 schema，成功后刷新并选中；校验失败不发请求', async () => {
    api.post.mockResolvedValueOnce({ data: { ...CONTRACT, id: 9, contract_no: 'SYN-LEASE-009' } })
    routeGet({
      '/finance/lease/contracts': { items: [{ ...CONTRACT, id: 9 }] },
      '/finance/lease/summary': { total_contracts: 1 },
      '/finance/lease/contracts/9': { ...CONTRACT, id: 9 },
    })
    const ctx = mounted(LeaseView)
    Object.assign(ctx.createForm, {
      contract_no: 'SYN-LEASE-009', name: '', lessee: '示例承租人',
      start_date: '2026-01-01', end_date: '2029-12-31', annual_rate: '0.035',
    })
    expect(ctx.canCreate.value).toBe(true)
    await ctx.createContract()
    expect(api.post).toHaveBeenCalledWith('/finance/lease/contracts', {
      ...CONTRACT_PAYLOAD, contract_no: 'SYN-LEASE-009', name: '', project: '', lessor: '', rate_basis: '',
    })
    expect(ctx.createVisible.value).toBe(false)
    expect(ctx.selected.value.id).toBe(9)
    expect(ElMessage.success).toHaveBeenCalled()

    const bad = mounted(LeaseView)
    Object.assign(bad.createForm, { contract_no: 'SYN-LEASE-010', lessee: '', annual_rate: '0.035' })
    await bad.createContract()
    expect(ElMessage.warning).toHaveBeenCalledWith('承租人主体不能为空')
  })

  it('selectContract 拉详情并清空已选测算、重置页签', async () => {
    routeGet({ '/finance/lease/contracts/5': CONTRACT })
    const ctx = mounted(LeaseView)
    ctx.selectedCalculation.value = CALC
    ctx.activeTab.value = 'checks'
    await ctx.selectContract(5)
    expect(ctx.selected.value).toEqual(CONTRACT)
    expect(ctx.selectedCalculation.value).toBeNull()
    expect(ctx.activeTab.value).toBe('contract')
  })

  it('删除/归档：确认后发请求；删除 409 时保持选择、不重复弹错', async () => {
    ElMessageBox.confirm.mockResolvedValue('confirm')
    routeGet({ '/finance/lease/contracts': { items: [] }, '/finance/lease/summary': {} })
    api.delete.mockRejectedValueOnce({ response: { status: 409, data: { detail: '合同已有 1 条测算历史' } } })
    api.patch.mockResolvedValueOnce({ data: { ...CONTRACT, status: 'archived', status_label: '已归档' } })
    const ctx = mounted(LeaseView)
    ctx.selected.value = CONTRACT
    await ctx.removeContract(CONTRACT)
    expect(api.delete).toHaveBeenCalledWith('/finance/lease/contracts/5')
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.selected.value.id).toBe(5)
    await ctx.archiveContract(CONTRACT)
    expect(api.patch).toHaveBeenCalledWith('/finance/lease/contracts/5', { status: 'archived' })
    expect(ElMessage.success).toHaveBeenCalled()
  })

  it('summary 缺失时用列表长度兜底；过期标记与利率格式化', () => {
    const ctx = mounted(LeaseView)
    ctx.contracts.value = [CONTRACT, { ...CONTRACT, id: 6 }]
    expect(ctx.summary.value.total_contracts).toBeUndefined()
    ctx.selected.value = { ...CONTRACT, latest_calculation_stale: true }
    expect(ctx.selectedStale.value).toBe(true)
    expect(ctx.formatRate('0.035')).toBe('3.50%')
    expect(ctx.formatRate(null)).toBe('—')
  })
})

describe('ContractPanel：草稿编辑与付款计划', () => {
  it('挂载拉取付款计划；无合同时不发请求（空态守卫）', async () => {
    routeGet({ '/finance/lease/contracts/5/payments': PAYMENTS })
    const { ctx } = mountPanel(ContractPanel, { contract: CONTRACT })
    expect(mountedCbs).toHaveLength(1)
    await fireMounted()
    expect(api.get).toHaveBeenCalledWith('/finance/lease/contracts/5/payments')
    expect(ctx.payments.value).toHaveLength(2)
    expect(ctx.payments.value[1].months).toBe(0)
    expect(ctx.totals.value).toEqual({ amount: '5000.00', months: 12 })

    vi.clearAllMocks()
    const empty = mounted(ContractPanel, { contract: null })
    await empty.loadPayments()
    expect(api.get).not.toHaveBeenCalled()
    expect(empty.payments.value).toEqual([])
  })

  it('保存合同：PATCH 固定 schema（remark）并通知父组件刷新', async () => {
    api.patch.mockResolvedValueOnce({ data: CONTRACT })
    const { ctx, events } = mountPanel(ContractPanel, { contract: CONTRACT })
    ctx.form.remark = '  带备注  '
    await ctx.saveContract()
    expect(api.patch).toHaveBeenCalledWith('/finance/lease/contracts/5', { ...CONTRACT_PAYLOAD, remark: '带备注' })
    expect(events.some((e) => e.name === 'refresh')).toBe(true)
  })

  it('已归档合同只读：保存/保存付款/导入都被拒绝且不发请求', async () => {
    const { ctx } = mountPanel(ContractPanel, { contract: { ...CONTRACT, status: 'archived' } })
    expect(ctx.canEdit.value).toBe(false)
    await ctx.saveContract()
    await ctx.savePayments()
    ctx.openImport()
    expect(api.patch).not.toHaveBeenCalled()
    expect(api.put).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
    expect(ctx.uploadDialog.value).toBe(false)
  })

  it('保存付款计划：整批替换，免租期/0 月期与标志原样保留', async () => {
    routeGet({ '/finance/lease/contracts/5/payments': PAYMENTS })
    api.put.mockResolvedValueOnce({ data: PAYMENTS })
    const { ctx, events } = mountPanel(ContractPanel, { contract: CONTRACT })
    ctx.payments.value = paymentsToEditor(PAYMENTS)
    await ctx.savePayments()
    expect(api.put).toHaveBeenCalledWith('/finance/lease/contracts/5/payments', {
      items: [
        { sequence: 1, source_row: 4, period_start: '2026-01-01', period_end: '2026-12-31', months: 12, amount: '5000.00', start_flag: true, prepay_flag: true, is_free_rent: false, note: '首期期初支付' },
        { sequence: 2, source_row: 5, period_start: '2027-01-01', period_end: '2027-12-31', months: 0, amount: '0.00', start_flag: false, prepay_flag: false, is_free_rent: true, note: '免租期' },
      ],
    })
    expect(events.some((e) => e.name === 'refresh')).toBe(true)
    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('整批替换'))
  })

  it('付款校验失败不发 PUT；加行/删行重排期次；空计划需确认后才清空', async () => {
    const { ctx } = mountPanel(ContractPanel, { contract: CONTRACT })
    ctx.payments.value = [
      { sequence: 2, period_start: '2026-01-01', period_end: '2026-12-31', months: 12, amount: 1 },
    ]
    await ctx.savePayments()
    expect(api.put).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('缺少第 1 期'))

    ctx.payments.value = []
    ctx.addPayment()
    expect(ctx.payments.value[0].sequence).toBe(1)
    ctx.addPayment()
    ctx.removePayment(0)
    expect(ctx.payments.value.map((r) => r.sequence)).toEqual([1])

    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    api.put.mockResolvedValueOnce({ data: [] })
    routeGet({ '/finance/lease/contracts/5/payments': [] })
    ctx.payments.value = []
    await ctx.savePayments()
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('清空'), expect.anything(), expect.anything(),
    )
    expect(api.put).toHaveBeenCalledWith('/finance/lease/contracts/5/payments', { items: [] })
  })

  it('导入预览：multipart 预览不落库（items/warnings/errors）；400 不重复弹错', async () => {
    api.post.mockResolvedValueOnce({ data: PREVIEW })
    const { ctx } = mountPanel(ContractPanel, { contract: CONTRACT })
    ctx.openImport()
    expect(ctx.uploadDialog.value).toBe(true)
    ctx.onFileChange({ raw: new Blob(['x']), name: 'payments-syn.xlsx' })
    await ctx.previewFile()
    const [url, formData, options] = api.post.mock.calls[0]
    expect(url).toBe('/finance/lease/contracts/5/payments/import/preview')
    expect(formData).toBeInstanceOf(FormData)
    expect(options).toMatchObject({ headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 })
    expect(ctx.previewRowList.value).toHaveLength(2)
    expect(ctx.previewWarningList.value).toHaveLength(1)
    expect(ctx.previewErrorList.value).toEqual([])
    expect(ctx.canApply.value).toBe(true)
    expect(ctx.previewSummaryText.value).toContain('预览不落库')

    const bad = mountPanel(ContractPanel, { contract: CONTRACT })
    api.post.mockRejectedValueOnce({ response: { status: 400, data: { detail: '表头缺「计租开始」' } } })
    bad.ctx.openImport()
    bad.ctx.onFileChange({ raw: new Blob(['x']), name: 'bad-syn.xlsx' })
    await bad.ctx.previewFile()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(bad.ctx.previewing.value).toBe(false)
    expect(bad.ctx.uploadDialog.value).toBe(true)
  })

  it('确认导入：免租/0 月期与提示先确认，随后重新上传同一文件才落库', async () => {
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    api.post.mockResolvedValueOnce({ data: { preview: PREVIEW, items: PAYMENTS } })
    routeGet({ '/finance/lease/contracts/5/payments': PAYMENTS })
    const { ctx, events } = mountPanel(ContractPanel, { contract: CONTRACT })
    ctx.openImport()
    ctx.onFileChange({ raw: new Blob(['x']), name: 'payments-syn.xlsx' })
    ctx.preview.value = PREVIEW
    await ctx.applyPreview()
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('免租期/0 月期'), expect.anything(), expect.anything(),
    )
    const [url, formData] = api.post.mock.calls[0]
    expect(url).toBe('/finance/lease/contracts/5/payments/import/apply')
    expect(formData).toBeInstanceOf(FormData)
    expect(ctx.uploadDialog.value).toBe(false)
    expect(ctx.preview.value).toBeNull()
    expect(events.some((e) => e.name === 'refresh')).toBe(true)
  })

  it('取消确认则不落库；错误行或无文件时禁止导入', async () => {
    ElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
    const { ctx } = mountPanel(ContractPanel, { contract: CONTRACT })
    ctx.openImport()
    ctx.onFileChange({ raw: new Blob(['x']), name: 'a.xlsx' })
    ctx.preview.value = PREVIEW
    await ctx.applyPreview()
    expect(api.post).not.toHaveBeenCalled()

    ctx.preview.value = { ...PREVIEW, errors: ['第 2 行：金额非法'] }
    expect(ctx.canApply.value).toBe(false)
    await ctx.applyPreview()
    expect(api.post).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('错误'))

    ctx.onFileRemove()
    expect(ctx.importFile.value).toBeNull()
    expect(ctx.preview.value).toBeNull()
  })
})

describe('MeasurePanel：测算、评估表与期间表', () => {
  const routes = {
    '/finance/lease/contracts/5/calculations': [{ ...CALC, id: 12, stale: true }, CALC],
    '/finance/lease/calculations/11': CALC,
    '/finance/lease/calculations/11/assessment': [ASSESS_ROW],
    '/finance/lease/calculations/11/periods': [PERIOD_ROW],
  }

  it('挂载加载测算列表并默认选「未过期」批次，同时取评估表与期间表', async () => {
    routeGet(routes)
    const { ctx, events } = mountPanel(MeasurePanel, { contract: CONTRACT })
    expect(mountedCbs).toHaveLength(1)
    await fireMounted()
    expect(ctx.calculations.value).toHaveLength(2)
    expect(ctx.activeCalculation.value.id).toBe(11)
    expect(ctx.assessmentRows.value).toEqual([ASSESS_ROW])
    expect(ctx.periodRows.value).toEqual([PERIOD_ROW])
    expect(events.find((e) => e.name === 'calculation').payload.id).toBe(11)
  })
  it('生成测算：POST 后刷新列表与明细；同 id 再次返回给幂等中文提示', async () => {
    api.post.mockResolvedValueOnce({ data: CALC })
    routeGet({ ...routes, '/finance/lease/contracts/5/calculations': [CALC] })
    const { ctx, events } = mountPanel(MeasurePanel, { contract: CONTRACT })
    await ctx.createCalculation()
    expect(api.post).toHaveBeenCalledWith('/finance/lease/contracts/5/calculations', null, { timeout: 120000 })
    expect(ctx.activeCalculation.value.id).toBe(11)
    expect(events.some((e) => e.name === 'refresh')).toBe(true)

    ctx.calculations.value = [CALC]
    api.post.mockResolvedValueOnce({ data: CALC })
    routeGet({ ...routes, '/finance/lease/contracts/5/calculations': [CALC] })
    await ctx.createCalculation()
    expect(ElMessage.info).toHaveBeenCalledWith(expect.stringContaining('幂等返回'))
  })
  it('测算 400（付款计划为空）：不重复弹错、不改已有结果', async () => {
    api.post.mockRejectedValueOnce({ response: { status: 400, data: { detail: '付款计划为空，不能测算' } } })
    const { ctx } = mountPanel(MeasurePanel, { contract: CONTRACT })
    ctx.activeCalculation.value = CALC
    await ctx.createCalculation()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.creating.value).toBe(false)
    expect(ctx.activeCalculation.value.id).toBe(11)
  })

  it('过期提示、金额与因子精度、汇总含付款期数与月数', () => {
    const { ctx } = mountPanel(MeasurePanel, { contract: CONTRACT })
    ctx.activeCalculation.value = { ...CALC, stale: true }
    expect(ctx.isStale.value).toBe(true)
    ctx.activeCalculation.value = CALC
    expect(ctx.isStale.value).toBe(false)
    expect(ctx.formatAmount('12345.6')).toBe('12345.60')
    expect(ctx.formatAmount(null)).toBe('—')
    expect(ctx.formatFactor('0.965456123')).toBe('0.965456123')
    expect(ctx.formatRate6('0.002917')).toBe('0.002917')
    expect(ctx.summaryRows.value.map((r) => r.label)).toEqual([
      '租赁负债现值', '使用权资产初始', '利息合计', '折旧合计', '付款合计', '付款期数', '期间数（月）', '首期 / 末期',
    ])
    // 首期简化模型：使用权资产初始必须等于租赁负债现值，不允许悄悄扩展
    expect(ctx.summaryRows.value[0].value).toBe(ctx.summaryRows.value[1].value)
    expect(ctx.summaryRows.value[6].value).toBe(36)
    // 期间表 lease_payment 是整期金额、期内每月重复，不能逐月合计
    expect(ctx.PERIOD_PAYMENT_HINT).toContain('整期金额')
    expect(ctx.PERIOD_PAYMENT_HINT).toContain('不能逐月求和')
  })

  it('无合同时不请求；pickDefault 优先未过期批次', async () => {
    const { ctx } = mountPanel(MeasurePanel, { contract: null })
    await ctx.loadCalculations()
    expect(api.get).not.toHaveBeenCalled()
    expect(ctx.pickDefaultId([])).toBeNull()
    expect(ctx.pickDefaultId([{ id: 1, stale: true }, { id: 2, stale: false }])).toBe(2)
  })
})

describe('CheckExportPanel：三态校验、确认与导出', () => {
  const calcRoutes = {
    '/finance/lease/contracts/5/calculations': [CALC],
    '/finance/lease/calculations/11/checks': CHECKS_OUT,
  }

  it('传入测算时只拉校验；三态计数与说明展示', async () => {
    routeGet(calcRoutes)
    const { ctx } = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    await fireMounted()
    expect(api.get).toHaveBeenCalledWith('/finance/lease/calculations/11/checks')
    expect(ctx.checkRowsData.value).toHaveLength(3)
    expect(ctx.overallMeta.value.type).toBe('success')
    expect(ctx.counts.value).toEqual({ ok: 3, failed: 0, unknown: 0 })
    expect(ctx.blockReason.value).toBe('')
    expect(ctx.valueText('12.3')).toBe('12.30')
    expect(ctx.valueText(null)).toBe('—')
  })

  it('差异/未知：结论非绿、按钮禁用并给出中文原因；接口失败按未知展示', async () => {
    routeGet({
      '/finance/lease/contracts/5/calculations': [CALC],
      '/finance/lease/calculations/11/checks': {
        ...CHECKS_OUT,
        checks: [
          { key: 'a', title: '负债滚动', status: 'ok', message: 'ok' },
          { key: 'b', title: '剩余付款', status: 'failed', message: '差 1.00', difference: '1.00' },
          { key: 'c', title: '月折旧', status: 'unknown', message: '缺期末余额' },
        ],
        check_counts: { ok: 1, failed: 1, unknown: 1 },
        all_passed: false,
        has_unknown: true,
      },
    })
    const { ctx } = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    await fireMounted()
    expect(ctx.checkRowsData.value[1].meta.type).toBe('danger')
    expect(ctx.checkRowsData.value[2].meta.type).toBe('warning')
    expect(ctx.unknownChecks.value[0].message).toContain('缺期末余额')
    expect(ctx.blockReason.value).toContain('差异')

    api.get.mockRejectedValue(new Error('boom'))
    const failed = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    await failed.ctx.loadChecksFor(11)
    expect(failed.ctx.checks.value.state).toBe('unknown')
    expect(failed.ctx.overallMeta.value.type).not.toBe('success')
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('未传测算时自行找当前批次并拉校验', async () => {
    routeGet({
      '/finance/lease/contracts/5/calculations': [{ ...CALC, id: 10, stale: true }, CALC],
      '/finance/lease/calculations/11': CALC,
      '/finance/lease/calculations/11/checks': CHECKS_OUT,
    })
    const { ctx } = mountPanel(CheckExportPanel, { contract: CONTRACT })
    await fireMounted()
    expect(ctx.activeCalculation.value.id).toBe(11)
    expect(ctx.selectedId.value).toBe(11)
    expect(api.get).toHaveBeenCalledWith('/finance/lease/calculations/11/checks')
  })

  it('确认成功：状态变 confirmed 并刷新；409 时不重复弹错且状态不变', async () => {
    routeGet(calcRoutes)
    api.post.mockResolvedValueOnce({ data: { ...CALC, status: 'confirmed', status_label: '已确认' } })
    const { ctx, events } = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    await ctx.loadChecksFor(11)
    await ctx.confirmCalculation()
    expect(api.post).toHaveBeenCalledWith('/finance/lease/calculations/11/confirm')
    expect(ctx.activeCalculation.value.status).toBe('confirmed')
    expect(ElMessage.success).toHaveBeenCalled()
    expect(events.some((e) => e.name === 'refresh')).toBe(true)

    routeGet(calcRoutes)
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: '测算结果已过期' } } })
    const conflict = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    await conflict.ctx.loadChecksFor(11)
    await conflict.ctx.confirmCalculation()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(conflict.ctx.activeCalculation.value.status).toBe('calculated')
    expect(conflict.ctx.confirming.value).toBe(false)
  })

  it('存在差异时不发 confirm，只给中文 warning', async () => {
    const { ctx } = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    ctx.checks.value = normalizeChecks({
      checks: [{ key: 'b', title: '剩余付款', status: 'failed', message: '差 1.00' }],
    })
    await ctx.confirmCalculation()
    expect(api.post).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('差异'))
  })

  it('过期且校验未通过：显示过期提示且禁止确认', () => {
    const { ctx } = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: { ...CALC, stale: true } })
    ctx.checks.value = normalizeChecks(CHECKS_OUT)
    expect(ctx.isStale.value).toBe(true)
    expect(ctx.blockReason.value).toContain('已过期')
  })
  it('导出（全部通过）：blob 请求无 allow_stale，文件名取后端响应头', async () => {
    const blob = new Blob(['xlsx'])
    api.get.mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': "attachment; filename*=utf-8''%E7%A7%9F%E8%B5%81%E6%8A%A5%E8%A1%A8.xlsx" },
    })
    const { ctx } = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    ctx.checks.value = normalizeChecks(CHECKS_OUT)
    await ctx.exportXlsx()
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(api.get).toHaveBeenCalledWith('/finance/lease/calculations/11/export.xlsx', {
      params: {}, responseType: 'blob', timeout: 120000,
    })
    expect(saveBlob).toHaveBeenCalledWith(blob, '租赁报表.xlsx')
  })

  it('过期/差异导出：警示后带 allow_stale=true；取消则不下载', async () => {
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    api.get.mockResolvedValue({ data: new Blob(['xlsx']), headers: {} })
    const { ctx } = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: { ...CALC, stale: true } })
    ctx.checks.value = normalizeChecks(CHECKS_OUT)
    await ctx.exportXlsx()
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('已过期'), expect.anything(), expect.anything(),
    )
    expect(api.get).toHaveBeenCalledWith('/finance/lease/calculations/11/export.xlsx', {
      params: { allow_stale: true }, responseType: 'blob', timeout: 120000,
    })
    expect(saveBlob).toHaveBeenCalledWith(expect.anything(), 'SYN-LEASE-001_租赁核算_2026-11-18.xlsx')

    const cancel = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    cancel.ctx.checks.value = normalizeChecks({
      checks: [{ key: 'b', title: '剩余付款', status: 'failed', message: '差 1.00' }],
    })
    ElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
    await cancel.ctx.exportXlsx()
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('导出被 409 拒绝：不保存文件、不重复弹错', async () => {
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    api.get.mockRejectedValueOnce({ response: { status: 409, data: { detail: '不能导出为当前底稿' } } })
    const { ctx } = mountPanel(CheckExportPanel, { contract: CONTRACT, calculation: CALC })
    ctx.checks.value = normalizeChecks({
      checks: [{ key: 'b', title: '剩余付款', status: 'failed', message: '差 1.00' }],
    })
    await ctx.exportXlsx()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(saveBlob).not.toHaveBeenCalled()
    expect(ctx.exporting.value).toBe(false)
  })

  it('空态：无合同/测算时零请求', async () => {
    const { ctx } = mountPanel(CheckExportPanel, { contract: null })
    expect(mountedCbs).toHaveLength(1)
    await fireMounted()
    expect(api.get).not.toHaveBeenCalled()
    expect(ctx.activeCalculation.value).toBeNull()
    expect(ctx.checkRowsData.value).toEqual([])
  })
})
