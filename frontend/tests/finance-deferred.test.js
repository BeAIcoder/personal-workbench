// 长期待摊前端：项目台账与参数维护、测算与月度摊销表、四态校验（通过/警告/差异/未知）、
// 风险显式确认、过期阻断与历史快照导出。
// node 环境无 DOM：沿用 finance-recon / finance-lease 惯例，mock 生命周期、消息组件、api
// 与下载实现，直接驱动编译后的 setup()，只验证行为（状态与请求），不渲染模板。
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

const manifest = (await import('../src/modules/finance-deferred/manifest.js')).default
const apiModule = await import('../src/modules/finance-deferred/api.js')
const DeferredView = (await import('../src/modules/finance-deferred/views/DeferredView.vue')).default
const ItemPanel = (await import('../src/modules/finance-deferred/views/ItemPanel.vue')).default
const SchedulePanel = (await import('../src/modules/finance-deferred/views/SchedulePanel.vue')).default
const RiskExportPanel = (await import('../src/modules/finance-deferred/views/RiskExportPanel.vue')).default
const { ElMessage, ElMessageBox } = await import('element-plus')
const {
  deferredApi,
  asList,
  itemStatusMeta,
  runStatusMeta,
  checkStateMeta,
  normalizeCounts,
  normalizeChecks,
  confirmBlockReason,
  riskNoteBlockReason,
  needsRiskAck,
  needsAllowStale,
  exportWarning,
  buildItemPayload,
  emptyItemForm,
  itemToForm,
  amortizationEndMonth,
  formatRate,
  formatMoney,
  exportFileName,
  exportFileNameFromResponse,
  filenameFromDisposition,
  isStaleRun,
  checkRows,
} = apiModule

function mountPanel(Component, props = {}) {
  const events = []
  const ctx = Component.setup(props, { expose: () => {}, emit: (name, payload) => events.push({ name, payload }) })
  return { ctx, events }
}
const mounted = (Component, props) => mountPanel(Component, props).ctx

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

async function fireMounted() {
  mountedCbs.splice(0).forEach((cb) => cb())
  await flush()
}

function routeGet(map) {
  api.get.mockImplementation((url) => {
    if (Object.prototype.hasOwnProperty.call(map, url)) return Promise.resolve({ data: map[url] })
    return Promise.reject(new Error(`未预期的 GET ${url}`))
  })
}

const ITEM = {
  id: 3, project_code: 'SYN-DEF-001', name: '合成改良项目', project: '合成项目',
  amount_incl_tax: '1356.00', tax_rate: '0.1300', amount_net: '1200.00', tax_amount: '156.00',
  start_month: '2026-01', months: 12, amortization_end_month: '2026-12',
  lease_end_date: '2026-12-31', remark: '', status: 'draft', status_label: '草稿',
  run_count: 1, confirmed_run_count: 0, latest_run_id: 7, latest_run_status: 'calculated',
  latest_run_stale: false, created_at: '2026-11-20T10:00:00', updated_at: '2026-11-20T10:00:00',
}
const ITEM_PAYLOAD = {
  project_code: 'SYN-DEF-001', name: '合成改良项目', project: '合成项目',
  amount_incl_tax: '1356.00', tax_rate: '0.1300', start_month: '2026-01', months: 12,
  lease_end_date: '2026-12-31', remark: '',
}
const SUMMARY_OK = {
  amount_incl_tax: '1356.00', tax_rate: '0.1300', tax_amount: '156.00', amount_net: '1200.00',
  monthly_amount: '100.00', months: 12, start_month: '2026-01', end_month: '2026-12',
  amortized_total: '1200.00', rounding_difference: '0.00', lease_end_date: '2026-12-31',
  expiry_after_lease: false, risk_flags: [], period_count: 12,
}
const CHECKS_OK = [
  { key: 'net_amount_formula', title: '净额 = 含税 ÷ (1 + 税率)', status: 'ok', expected: '1200.00', actual: '1200.00', difference: '0.00', message: '' },
  { key: 'amortization_total', title: 'Σ月摊 = 净额（末期尾差如实保留）', status: 'ok', expected: '1200.00', actual: '1200.00', difference: '0.00', message: '' },
  { key: 'lease_expiry_coverage', title: '摊销到期不晚于租赁到期日', status: 'ok', expected: null, actual: null, difference: null, message: '摊销到期月 2026-12 不晚于租赁到期日 2026-12-31' },
]
const RUN = {
  id: 7, item_id: 3, status: 'calculated', status_label: '已测算', input_hash: 'a'.repeat(64),
  rule_version: 'deferred-mvp-1', summary: SUMMARY_OK, checks: CHECKS_OK,
  check_counts: { ok: 9, warning: 0, failed: 0, unknown: 0 }, all_checks_passed: true,
  has_warnings: false, has_unknown_checks: false, stale: false, risk_acknowledged: false,
  risk_note: '', confirmed_at: null, created_at: '2026-11-20T10:00:00',
  updated_at: '2026-11-20T10:00:00',
}
const WARN_SUMMARY = {
  ...SUMMARY_OK, amount_incl_tax: '1000.00', tax_rate: '0.0000', tax_amount: '0.00',
  amount_net: '1000.00', monthly_amount: '333.33', months: 3, end_month: '2026-03',
  amortized_total: '999.99', rounding_difference: '0.01', period_count: 3,
  risk_flags: ['末期尾差 0.01 元如实留在末期余额，未吸差、未自动转销'],
}
const WARN_RUN = {
  ...RUN, summary: WARN_SUMMARY,
  checks: [
    { key: 'amortization_total', title: 'Σ月摊 = 净额（末期尾差如实保留）', status: 'warning', expected: '1000.00', actual: '999.99', difference: '-0.01', message: '末期尾差 -0.01 元：已如实留在末期余额，未吸收进末期、未自动转销' },
    { key: 'net_amount_formula', title: '净额 = 含税 ÷ (1 + 税率)', status: 'ok', expected: '1000.00', actual: '1000.00', difference: '0.00', message: '' },
  ],
  check_counts: { ok: 1, warning: 1, failed: 0, unknown: 0 }, has_warnings: true,
}
const PERIODS = [
  { id: 1, period_month: '2026-01', sequence: 1, monthly_amount: '100.00', cumulative: '100.00', balance: '1100.00', remaining_months: 11 },
  { id: 2, period_month: '2026-02', sequence: 2, monthly_amount: '100.00', cumulative: '200.00', balance: '1000.00', remaining_months: 10 },
]
const CHECKS_OUT = {
  run_id: 7, input_hash: RUN.input_hash, rule_version: RUN.rule_version, stale: false,
  checks: CHECKS_OK, check_counts: { ok: 9, warning: 0, failed: 0, unknown: 0 },
  all_passed: true, has_warnings: false, has_unknown: false,
}

beforeAll(() => {
  if (!URL.createObjectURL) URL.createObjectURL = () => 'blob:stub'
  if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {}
})

beforeEach(() => {
  vi.resetAllMocks()
  api.defaults = { baseURL: '/api' }
  mountedCbs.length = 0
  ElMessageBox.confirm.mockResolvedValue(true)
})

describe('模块注册与 API 约定', () => {
  it('路由 /finance/deferred，菜单在财务/账务处理 order 99', () => {
    expect(manifest.name).toBe('finance-deferred')
    expect(manifest.routes).toHaveLength(1)
    expect(manifest.routes[0]).toMatchObject({ path: '/finance/deferred', name: 'finance-deferred' })
    expect(typeof manifest.routes[0].component).toBe('function')
    expect(manifest.menu).toMatchObject({
      group: '财务', subgroup: '账务处理', title: '长期待摊',
      path: '/finance/deferred', order: 99, icon: 'Tickets',
    })
  })

  it('菜单图标在 main.js 字符串图标注册表中（<component :is> 才能渲染）', () => {
    const source = readFileSync(fileURLToPath(new URL('../src/main.js', import.meta.url)), 'utf8')
    const start = source.indexOf('const STRING_ICONS = {')
    expect(start).toBeGreaterThan(-1)
    expect(source.slice(start, source.indexOf('}', start))).toContain(manifest.menu.icon)
  })

  it('deferredApi 路径不带 /api 前缀；导出默认不带 allow_stale', async () => {
    api.get.mockResolvedValue({ data: {} })
    api.post.mockResolvedValue({ data: {} })
    api.patch.mockResolvedValue({ data: {} })
    api.delete.mockResolvedValue({ data: {} })
    await deferredApi.items({ page: 1, page_size: 200 })
    await deferredApi.item(3)
    await deferredApi.patchItem(3, {})
    await deferredApi.deleteItem(3)
    await deferredApi.runs(3)
    await deferredApi.createRun(3)
    await deferredApi.run(7)
    await deferredApi.periods(7)
    await deferredApi.checks(7)
    await deferredApi.confirm(7, { risk_acknowledged: true, risk_note: '合成说明' })
    await deferredApi.exportXlsx(7)
    await deferredApi.exportXlsx(7, { allowStale: true })
    await deferredApi.summary()
    expect(api.get.mock.calls.map((call) => call[0])).toEqual([
      '/finance/deferred/items',
      '/finance/deferred/items/3',
      '/finance/deferred/items/3/runs',
      '/finance/deferred/runs/7',
      '/finance/deferred/runs/7/periods',
      '/finance/deferred/runs/7/checks',
      '/finance/deferred/runs/7/export.xlsx',
      '/finance/deferred/runs/7/export.xlsx',
      '/finance/deferred/summary',
    ])
    expect(api.get).toHaveBeenCalledWith('/finance/deferred/runs/7/export.xlsx', {
      params: {}, responseType: 'blob', timeout: 120000,
    })
    expect(api.get).toHaveBeenCalledWith('/finance/deferred/runs/7/export.xlsx', {
      params: { allow_stale: true }, responseType: 'blob', timeout: 120000,
    })
    expect(api.post).toHaveBeenCalledWith('/finance/deferred/items/3/runs', null, { timeout: 120000 })
    expect(api.post).toHaveBeenCalledWith('/finance/deferred/runs/7/confirm', {
      risk_acknowledged: true, risk_note: '合成说明',
    })
  })
})

describe('状态映射：未知/差异一律非绿', () => {
  it('四态映射与未知枚举回退', () => {
    expect(checkStateMeta('ok')).toMatchObject({ label: '通过', type: 'success' })
    expect(checkStateMeta('warning')).toMatchObject({ label: '警告', type: 'warning' })
    expect(checkStateMeta('failed')).toMatchObject({ label: '差异', type: 'danger' })
    expect(checkStateMeta('unknown')).toMatchObject({ label: '未知', type: 'warning' })
    expect(checkStateMeta('weird').type).toBe('warning')
    expect(itemStatusMeta('draft').label).toBe('草稿')
    expect(itemStatusMeta('archived').label).toBe('已归档')
    expect(itemStatusMeta('weird').type).toBe('warning')
    expect(runStatusMeta('calculated').type).toBe('info')
    expect(runStatusMeta('confirmed').type).toBe('success')
    expect(runStatusMeta('weird').type).toBe('warning')
  })

  it('normalizeCounts 兼容 run/checks/裸计数；normalizeChecks 定总结论', () => {
    expect(normalizeCounts(RUN)).toEqual({ ok: 9, warning: 0, failed: 0, unknown: 0 })
    expect(normalizeCounts(WARN_RUN)).toEqual({ ok: 1, warning: 1, failed: 0, unknown: 0 })
    expect(normalizeCounts({ ok: -1, warning: 'x' })).toEqual({ ok: 0, warning: 0, failed: 0, unknown: 0 })
    expect(normalizeChecks({ checks: CHECKS_OK }).state).toBe('ok')
    expect(normalizeChecks(WARN_RUN).state).toBe('warning')
    expect(normalizeChecks({ checks: [{ status: 'failed' }] }).state).toBe('failed')
    expect(normalizeChecks({ checks: [{ status: 'unknown' }] }).state).toBe('unknown')
    expect(normalizeChecks({ checks: [] }).state).toBe('unknown')
    expect(checkRows(CHECKS_OK)[0].meta.label).toBe('通过')
    expect(asList({ items: [ITEM] })).toHaveLength(1)
    expect(asList([RUN])).toHaveLength(1)
  })
})

describe('项目请求体与展示口径', () => {
  it('buildItemPayload 校验必填/数值/期间/月数/日期并规整小数', () => {
    const form = { ...emptyItemForm(), project_code: 'SYN-DEF-001', name: '合成改良项目',
      project: '合成项目', amount_incl_tax: '1356', tax_rate: '0.13', start_month: '2026-01',
      months: 12, lease_end_date: '2026-12-31' }
    expect(buildItemPayload(form)).toEqual(ITEM_PAYLOAD)
    expect(() => buildItemPayload({ ...form, project_code: ' ' })).toThrow('项目编码')
    expect(() => buildItemPayload({ ...form, name: '' })).toThrow('项目名称')
    expect(() => buildItemPayload({ ...form, project: '' })).toThrow('所属项目')
    expect(() => buildItemPayload({ ...form, amount_incl_tax: '' })).toThrow('含税金额不能为空')
    expect(() => buildItemPayload({ ...form, amount_incl_tax: -1 })).toThrow('不小于 0')
    expect(() => buildItemPayload({ ...form, tax_rate: '' })).toThrow('税率不能为空')
    expect(() => buildItemPayload({ ...form, tax_rate: 1.5 })).toThrow('0~1')
    expect(() => buildItemPayload({ ...form, start_month: '2026-13' })).toThrow('YYYY-MM')
    expect(() => buildItemPayload({ ...form, months: 0 })).toThrow('1~600')
    expect(() => buildItemPayload({ ...form, months: 601 })).toThrow('1~600')
    expect(() => buildItemPayload({ ...form, lease_end_date: '2026/12/31' })).toThrow('YYYY-MM-DD')
    // 免税：税率 0 合法
    expect(buildItemPayload({ ...form, tax_rate: 0 }).tax_rate).toBe('0.0000')
  })

  it('itemToForm/空表单与摊销到期月推算', () => {
    const form = itemToForm(ITEM)
    expect(form.amount_incl_tax).toBe(1356)
    expect(form.months).toBe(12)
    expect(emptyItemForm().tax_rate).toBe(0.06)
    expect(amortizationEndMonth('2026-01', 12)).toBe('2026-12')
    expect(amortizationEndMonth('2026-11', 3)).toBe('2027-01')
    expect(amortizationEndMonth('2025-12', 1)).toBe('2025-12')
    expect(amortizationEndMonth('2026-13', 12)).toBe('')
    expect(amortizationEndMonth('2026-01', 0)).toBe('')
    expect(formatRate('0.1300')).toBe('13.00%')
    expect(formatRate(null)).toBe('—')
    expect(formatMoney('1200.5')).toBe('1200.50')
    expect(formatMoney(undefined)).toBe('—')
  })
})

describe('确认/导出前置判断', () => {
  it('confirmBlockReason 覆盖未选择/已确认/过期/差异/未知/可确认', () => {
    expect(confirmBlockReason(null)).toContain('请先选择')
    expect(confirmBlockReason({ ...RUN, status: 'confirmed' })).toContain('不可改写')
    expect(confirmBlockReason({ ...RUN, status: 'weird' })).toContain('不可确认')
    expect(confirmBlockReason({ ...RUN, stale: true })).toContain('已过期')
    expect(confirmBlockReason(RUN, { ok: 8, warning: 0, failed: 1, unknown: 0 })).toContain('差异')
    expect(confirmBlockReason(RUN, { ok: 8, warning: 0, failed: 0, unknown: 1 })).toContain('未知')
    expect(confirmBlockReason(RUN, { ok: 0, warning: 0, failed: 0, unknown: 0 })).toContain('没有可确认')
    expect(confirmBlockReason(RUN)).toBe('')
    expect(confirmBlockReason(WARN_RUN)).toBe('')
  })

  it('风险确认必须勾选且写明说明', () => {
    expect(needsRiskAck(WARN_RUN)).toBe(true)
    expect(needsRiskAck(RUN)).toBe(false)
    expect(riskNoteBlockReason(WARN_RUN, null, false, '')).toContain('必须勾选')
    expect(riskNoteBlockReason(WARN_RUN, null, true, '   ')).toContain('风险说明为空')
    expect(riskNoteBlockReason(WARN_RUN, null, true, '接受尾差')).toBe('')
    expect(riskNoteBlockReason(RUN, null, false, '')).toBe('')
  })

  it('过期/差异需 allow_stale，警告允许导出但提示', () => {
    expect(isStaleRun(RUN)).toBe(false)
    expect(needsAllowStale(RUN)).toBe(false)
    expect(needsAllowStale({ ...RUN, stale: true })).toBe(true)
    expect(needsAllowStale(RUN, { ok: 0, warning: 0, failed: 1, unknown: 0 })).toBe(true)
    expect(exportWarning(RUN, { ok: 9, warning: 0, failed: 0, unknown: 0 })).toBe('')
    expect(exportWarning({ ...RUN, stale: true })).toContain('409')
    expect(exportWarning(WARN_RUN)).toContain('风险警告')
  })

  it('文件名：后端 UTF-8 优先，退回本地命名', () => {
    expect(filenameFromDisposition('attachment; filename="deferred.xlsx"; filename*=UTF-8\'\'%E9%95%BF%E6%9C%9F.xlsx'))
      .toBe('长期.xlsx')
    expect(filenameFromDisposition('attachment; filename="deferred_1.xlsx"')).toBe('deferred_1.xlsx')
    expect(filenameFromDisposition('')).toBe('')
    expect(exportFileName(ITEM, RUN)).toBe('SYN-DEF-001_长期待摊_2026-11-20.xlsx')
    expect(exportFileNameFromResponse({ headers: { 'content-disposition': 'attachment; filename="a.xlsx"' } }, ITEM, RUN))
      .toBe('a.xlsx')
    expect(exportFileNameFromResponse({ headers: {} }, ITEM, RUN)).toBe('SYN-DEF-001_长期待摊_2026-11-20.xlsx')
  })
})

describe('项目台账（DeferredView）', () => {
  it('挂载即请求台账与摘要，选择项目后拉详情', async () => {
    routeGet({
      '/finance/deferred/items': { total: 1, items: [ITEM] },
      '/finance/deferred/summary': { total_items: 1, draft_count: 1, archived_count: 0, items_with_pending: 0, items: [] },
      '/finance/deferred/items/3': ITEM,
    })
    const view = mounted(DeferredView)
    await fireMounted()
    expect(view.items.value).toHaveLength(1)
    expect(view.summary.value.total_items).toBe(1)
    await view.selectItem(ITEM)
    expect(view.selected.value.id).toBe(3)
    expect(view.selectedStale.value).toBe(false)
  })

  it('新建项目提交规整请求体并选中新项目', async () => {
    routeGet({
      '/finance/deferred/items': { total: 0, items: [] },
      '/finance/deferred/summary': { total_items: 0, draft_count: 0, archived_count: 0 },
      '/finance/deferred/items/3': ITEM,
    })
    api.post.mockResolvedValue({ data: ITEM })
    const view = mounted(DeferredView)
    await fireMounted()
    view.openCreate()
    Object.assign(view.createForm, {
      project_code: 'SYN-DEF-001', name: '合成改良项目', project: '合成项目',
      amount_incl_tax: '1356', tax_rate: '0.13', start_month: '2026-01', months: 12,
      lease_end_date: '2026-12-31',
    })
    expect(view.canCreate.value).toBe(true)
    await view.createItem()
    expect(api.post).toHaveBeenCalledWith('/finance/deferred/items', ITEM_PAYLOAD)
    expect(view.createVisible.value).toBe(false)
    expect(view.selected.value.id).toBe(3)
  })

  it('非法表单只提示不发请求；请求失败不崩溃', async () => {
    routeGet({ '/finance/deferred/items': { total: 0, items: [] }, '/finance/deferred/summary': {} })
    const view = mounted(DeferredView)
    await fireMounted()
    view.openCreate()
    Object.assign(view.createForm, { project_code: 'SYN', amount_incl_tax: 'x' })
    await view.createItem()
    expect(ElMessage.warning).toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()

    api.get.mockRejectedValue(new Error('boom'))
    await view.loadItems()
    expect(view.itemsLoading.value).toBe(false)
  })

  it('归档与删除确认后调用后端，取消则不请求', async () => {
    routeGet({
      '/finance/deferred/items': { total: 1, items: [ITEM] },
      '/finance/deferred/summary': {},
      '/finance/deferred/items/3': ITEM,
    })
    api.patch.mockResolvedValue({ data: { ...ITEM, status: 'archived' } })
    api.delete.mockResolvedValue({ data: { deleted: true } })
    const view = mounted(DeferredView)
    await fireMounted()
    await view.selectItem(ITEM)
    await view.archiveItem(ITEM)
    expect(api.patch).toHaveBeenCalledWith('/finance/deferred/items/3', { status: 'archived' })

    ElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
    await view.removeItem(ITEM)
    expect(api.delete).not.toHaveBeenCalled()
    await view.removeItem(ITEM)
    expect(api.delete).toHaveBeenCalledWith('/finance/deferred/items/3')
    expect(view.selected.value).toBeNull()
  })
})

describe('参数维护（ItemPanel）', () => {
  it('草稿可打开编辑并保存完整请求体', async () => {
    api.patch.mockResolvedValue({ data: ITEM })
    const { ctx, events } = mountPanel(ItemPanel, { item: ITEM })
    ctx.startEdit()
    expect(ctx.editVisible.value).toBe(true)
    expect(ctx.canSave.value).toBe(true)
    expect(ctx.previewEndMonth.value).toBe('2026-12')
    ctx.form.amount_incl_tax = '2000'
    await ctx.save()
    expect(api.patch).toHaveBeenCalledWith('/finance/deferred/items/3', {
      ...ITEM_PAYLOAD, amount_incl_tax: '2000.00',
    })
    expect(events.some((event) => event.name === 'refresh')).toBe(true)
    expect(ctx.editVisible.value).toBe(false)
  })

  it('归档项目不可编辑；非法金额只提示不发请求', async () => {
    const archived = { ...ITEM, status: 'archived', status_label: '已归档' }
    const { ctx } = mountPanel(ItemPanel, { item: archived })
    ctx.startEdit()
    expect(ctx.editVisible.value).toBe(false)
    expect(ElMessage.warning).toHaveBeenCalled()

    const draft = mountPanel(ItemPanel, { item: ITEM })
    draft.ctx.form.amount_incl_tax = ''
    await draft.ctx.save()
    expect(api.patch).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
  })
})

describe('测算与月度表（SchedulePanel）', () => {
  it('挂载加载测算与期间行并回传选中批次', async () => {
    routeGet({
      '/finance/deferred/items/3/runs': [RUN],
      '/finance/deferred/runs/7/periods': PERIODS,
    })
    const { ctx, events } = mountPanel(SchedulePanel, { item: ITEM })
    await fireMounted()
    expect(ctx.runs.value).toHaveLength(1)
    expect(ctx.selectedRun.value.id).toBe(7)
    expect(ctx.periods.value).toHaveLength(2)
    const change = events.find((event) => event.name === 'run-change')
    expect(change.payload.id).toBe(7)
  })

  it('生成测算：调用 createRun、重载并刷新台账', async () => {
    routeGet({
      '/finance/deferred/items/3/runs': [RUN],
      '/finance/deferred/runs/7/periods': PERIODS,
    })
    api.post.mockResolvedValue({ data: RUN })
    const { ctx, events } = mountPanel(SchedulePanel, { item: ITEM })
    await fireMounted()
    await ctx.runCalculation()
    expect(api.post).toHaveBeenCalledWith('/finance/deferred/items/3/runs', null, { timeout: 120000 })
    expect(events.some((event) => event.name === 'refresh')).toBe(true)
    expect(ctx.running.value).toBe(false)
  })

  it('归档项目不发起测算', async () => {
    routeGet({ '/finance/deferred/items/3/runs': [] })
    const { ctx } = mountPanel(SchedulePanel, { item: { ...ITEM, status: 'archived' } })
    await fireMounted()
    expect(ctx.runs.value).toHaveLength(0)
    await ctx.runCalculation()
    expect(api.post).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
  })
})

describe('风险/确认/导出（RiskExportPanel）', () => {
  it('存在警告时未勾选/未写说明不提交确认', async () => {
    const { ctx } = mountPanel(RiskExportPanel, { item: ITEM, run: WARN_RUN })
    expect(ctx.warningCount.value).toBe(1)
    expect(ctx.blockReason.value).toBe('')
    expect(ctx.riskBlockReason.value).toContain('必须勾选')
    await ctx.confirmRun()
    expect(api.post).not.toHaveBeenCalled()

    ctx.riskAcknowledged.value = true
    await ctx.confirmRun()
    expect(ctx.riskBlockReason.value).toContain('风险说明为空')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('勾选并填写说明后确认，提交风险留痕并刷新', async () => {
    api.post.mockResolvedValue({
      data: { ...WARN_RUN, status: 'confirmed', status_label: '已确认', risk_acknowledged: true, risk_note: '接受尾差' },
    })
    api.get.mockResolvedValue({ data: { ...CHECKS_OUT } })
    const { ctx, events } = mountPanel(RiskExportPanel, { item: ITEM, run: WARN_RUN })
    ctx.riskAcknowledged.value = true
    ctx.riskNote.value = ' 接受尾差 '
    await ctx.confirmRun()
    expect(api.post).toHaveBeenCalledWith('/finance/deferred/runs/7/confirm', {
      risk_acknowledged: true, risk_note: '接受尾差',
    })
    expect(events.some((event) => event.name === 'run-change')).toBe(true)
    expect(events.some((event) => event.name === 'refresh')).toBe(true)
  })

  it('过期批次导出走历史快照：确认后带 allow_stale 并 saveBlob 落名', async () => {
    api.get.mockResolvedValue({
      data: new Blob(['x']),
      headers: { 'content-disposition': 'attachment; filename="deferred_7.xlsx"; filename*=UTF-8\'\'%E9%95%BF%E6%9C%9F%E5%BE%85%E6%91%8A.xlsx' },
    })
    const staleRun = { ...RUN, stale: true }
    const { ctx } = mountPanel(RiskExportPanel, { item: ITEM, run: staleRun })
    await ctx.exportWorkbook()
    expect(ElMessageBox.confirm).toHaveBeenCalled()
    expect(api.get).toHaveBeenCalledWith('/finance/deferred/runs/7/export.xlsx', {
      params: { allow_stale: true }, responseType: 'blob', timeout: 120000,
    })
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), '长期待摊.xlsx')

    ElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
    await ctx.exportWorkbook()
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('无风险的正常批次直接导出（不弹确认）；未选择批次给出提示', async () => {
    api.get.mockResolvedValue({ data: new Blob(['x']), headers: {} })
    const { ctx } = mountPanel(RiskExportPanel, { item: ITEM, run: RUN })
    await ctx.exportWorkbook()
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(api.get).toHaveBeenCalledWith('/finance/deferred/runs/7/export.xlsx', {
      params: {}, responseType: 'blob', timeout: 120000,
    })
    expect(saveBlob).toHaveBeenCalled()

    const empty = mountPanel(RiskExportPanel, { item: ITEM, run: null })
    await empty.ctx.exportWorkbook()
    expect(ElMessage.warning).toHaveBeenCalled()
    expect(empty.ctx.blockReason.value).toContain('请先选择')
  })

  it('刷新校验：合并 checks/计数，过期标记跟随后端', async () => {
    api.get.mockResolvedValue({ data: { ...CHECKS_OUT, stale: true, check_counts: { ok: 8, warning: 1, failed: 0, unknown: 0 } } })
    const { ctx } = mountPanel(RiskExportPanel, { item: ITEM, run: WARN_RUN })
    await ctx.reload()
    expect(ctx.counts.value).toEqual({ ok: 8, warning: 1, failed: 0, unknown: 0 })
    expect(ctx.activeRun.value.stale).toBe(true)
    expect(ctx.rows.value).toHaveLength(3)
    expect(api.get).toHaveBeenCalledWith('/finance/deferred/runs/7/checks')
  })
})
