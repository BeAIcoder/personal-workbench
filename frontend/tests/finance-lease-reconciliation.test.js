// 租赁核算「BIP对账」页签：科目映射编辑/保存、上下文加载、执行对账、差异与未知原因展示。
// node 环境无 DOM：沿用 finance-lease / finance-recon 惯例，mock 生命周期、消息组件与
// api，直接驱动编译后的 setup()，只验证行为（请求参数与状态），不渲染模板。
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))
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
  saveBlob: vi.fn(() => true),
  openPrintWindow: vi.fn(),
  showPdfInWindow: vi.fn(),
  REVOKE_DELAY_MS: 10000,
  PRINT_REVOKE_DELAY_MS: 600000,
}))

const apiModule = await import('../src/modules/finance-lease/api.js')
const ReconciliationPanel = (
  await import('../src/modules/finance-lease/views/ReconciliationPanel.vue')
).default
const LeaseView = (await import('../src/modules/finance-lease/views/LeaseView.vue')).default
const { ElMessage } = await import('element-plus')
const {
  leaseApi,
  RECON_SUBJECT_KEYS,
  buildMappingsPayload,
  coverageMeta,
  emptyMappingRows,
  formatAmount,
  mappingsToRows,
  normalizeReconcile,
  reconCheckBlockReason,
  reconStatusMeta,
} = apiModule

const API = '/finance/lease'

const CONTEXT = {
  lessees: ['合成承租人甲', '合成承租人乙'],
  ledgers: ['合成账簿'],
  periods: ['2026-03', '2026-06'],
  subject_keys: RECON_SUBJECT_KEYS.map(({ key, label }) => ({ key, label })),
  directions: [
    { value: 'debit', label: '借方' },
    { value: 'credit', label: '贷方' },
  ],
  mappings: [],
}

const MAPPINGS = [
  { id: 1, lessee: '合成承租人甲', ledger: '合成账簿', subject_key: 'rou_asset', subject_key_label: '使用权资产原值', subject_code: '1801', direction: 'debit', direction_label: '借方', updated_at: '2026-11-20T10:00:00' },
  { id: 2, lessee: '合成承租人甲', ledger: '合成账簿', subject_key: 'rou_accum_dep', subject_key_label: '使用权资产累计折旧', subject_code: '1802', direction: 'credit', direction_label: '贷方', updated_at: '2026-11-20T10:00:00' },
  { id: 3, lessee: '合成承租人甲', ledger: '合成账簿', subject_key: 'lease_payment', subject_key_label: '租赁付款额（剩余付款）', subject_code: '2701', direction: 'credit', direction_label: '贷方', updated_at: '2026-11-20T10:00:00' },
]

const SUBJECTS = [
  { subject_key: 'rou_asset', subject_key_label: '使用权资产原值', subject_code: '1801', direction: 'debit', direction_label: '借方', expected: '570.00', expected_source: 'lease_snapshot', actual: '570.00', difference: '0.00', status: 'ok', status_label: '通过', source: 'bip_closing_direction', source_label: 'BIP 期末借方/贷方之差', reason: '' },
  { subject_key: 'rou_accum_dep', subject_key_label: '使用权资产累计折旧', subject_code: '1802', direction: 'credit', direction_label: '贷方', expected: '285.00', expected_source: 'lease_snapshot', actual: '285.02', difference: '-0.02', status: 'failed', status_label: '差异', source: 'bip_closing_direction', source_label: 'BIP 期末借方/贷方之差', reason: '差异 -0.02 超过容差 0.01' },
  { subject_key: 'lease_payment', subject_key_label: '租赁付款额（剩余付款）', subject_code: '2701', direction: 'credit', direction_label: '贷方', expected: '300.00', expected_source: 'lease_snapshot', actual: '300.00', difference: '0.00', status: 'ok', status_label: '通过', source: 'bip_closing_direction', source_label: 'BIP 期末借方/贷方之差', reason: '' },
  { subject_key: 'unrecognized_finance', subject_key_label: '未确认融资费用余额', subject_code: null, direction: null, direction_label: null, expected: '18.00', expected_source: 'lease_snapshot', actual: null, difference: null, status: 'unknown', status_label: '未知', source: 'unmapped', source_label: '未配置科目映射', reason: '未配置「未确认融资费用余额」的 BIP 科目映射（承租人「合成承租人甲」/ 账簿「合成账簿」）' },
  { subject_key: 'lease_liability', subject_key_label: '租赁负债净额', subject_code: '2703', direction: 'credit', direction_label: '贷方', expected: '282.00', expected_source: 'lease_snapshot', actual: '282.00', difference: '0.00', status: 'ok', status_label: '通过', source: 'bip_closing_balance', source_label: 'BIP 期末余额（方向金额均为空，按映射方向取绝对值）', reason: '' },
]

const CHECK_RESULT = {
  lessee: '合成承租人甲',
  ledger: '合成账簿',
  period: '2026-03',
  tolerance: '0.01',
  status: 'failed',
  status_label: '差异',
  counts: { ok: 3, failed: 1, unknown: 1 },
  subjects: SUBJECTS,
  contracts: [
    { contract_id: 5, contract_no: 'SYN-LEASE-901', name: '合成租赁合同', contract_status: 'draft', selected: true, calculation_id: 11, calculation_status: 'confirmed', calculation_input_hash: 'a'.repeat(64), calculation_rule_version: 'lease-rules-1', stale: false, covers_period: true, reason: '', present_value: '570.00', accumulated_depreciation: '285.00', remaining_payment: '300.00', unrecognized_finance: '18.00', liability_balance: '282.00' },
    { contract_id: 6, contract_no: 'SYN-LEASE-902', name: '合成租赁合同二', contract_status: 'draft', selected: false, calculation_id: 12, calculation_status: 'confirmed', calculation_input_hash: 'b'.repeat(64), calculation_rule_version: 'lease-rules-1', stale: true, covers_period: false, reason: '已确认测算已过期：合同或付款计划在测算后变更，请重新测算并确认', present_value: null, accumulated_depreciation: null, remaining_payment: null, unrecognized_finance: null, liability_balance: null },
  ],
  coverage: {
    contract_count: 2,
    selected_count: 1,
    unknown_count: 1,
    reasons: ['已确认测算已过期：合同或付款计划在测算后变更，请重新测算并确认'],
  },
  generated_at: '2026-11-20T10:00:00',
}

function mountPanel(Component, props = {}) {
  const events = []
  const ctx = Component.setup(props, {
    expose: () => {},
    emit: (name, payload) => events.push({ name, payload }),
  })
  return { ctx, events }
}

async function fireMounted() {
  mountedCbs.splice(0).forEach((cb) => cb())
  await new Promise((resolve) => setTimeout(resolve, 0))
  await new Promise((resolve) => setTimeout(resolve, 0))
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

describe('API 路径与科目位契约', () => {
  it('对账端点不带 /api 前缀，上下文无额外参数', async () => {
    api.get.mockResolvedValue({ data: {} })
    api.put.mockResolvedValue({ data: {} })
    api.post.mockResolvedValue({ data: {} })
    await leaseApi.reconciliationContext()
    await leaseApi.reconciliationMappings({ lessee: '合成承租人甲', ledger: '合成账簿' })
    await leaseApi.putReconciliationMappings({ lessee: '合成承租人甲', ledger: '合成账簿', items: [] })
    await leaseApi.reconciliationCheck({ lessee: '合成承租人甲', ledger: '合成账簿', period: '2026-03' })
    expect(api.get.mock.calls.map((call) => call[0])).toEqual([
      `${API}/reconciliation/context`,
      `${API}/reconciliation/mappings`,
    ])
    expect(api.get.mock.calls[1][1]).toEqual({
      params: { lessee: '合成承租人甲', ledger: '合成账簿' },
    })
    expect(api.put).toHaveBeenCalledWith(`${API}/reconciliation/mappings`, {
      lessee: '合成承租人甲', ledger: '合成账簿', items: [],
    })
    expect(api.post).toHaveBeenCalledWith(`${API}/reconciliation/check`, {
      lessee: '合成承租人甲', ledger: '合成账簿', period: '2026-03',
    })
  })

  it('科目位固定五类，默认方向与会计口径一致', () => {
    expect(RECON_SUBJECT_KEYS.map((item) => item.key)).toEqual([
      'rou_asset', 'rou_accum_dep', 'lease_payment', 'unrecognized_finance', 'lease_liability',
    ])
    const rows = emptyMappingRows()
    expect(rows).toHaveLength(5)
    expect(rows.map((row) => row.direction)).toEqual([
      'debit', 'credit', 'credit', 'debit', 'credit',
    ])
    expect(rows.every((row) => row.subject_code === '')).toBe(true)
  })

  it('mappingsToRows 只填已知科目位，不臆造后端没有的科目', () => {
    const rows = mappingsToRows(MAPPINGS)
    expect(rows).toHaveLength(5)
    expect(rows[0]).toMatchObject({ subject_key: 'rou_asset', subject_code: '1801', direction: 'debit' })
    expect(rows[1]).toMatchObject({ subject_code: '1802', direction: 'credit' })
    expect(rows[3].subject_code).toBe('')
    expect(rows[3].direction).toBe('debit')
  })
})

describe('映射请求体与对账前置校验', () => {
  it('buildMappingsPayload 修剪空白、跳过空科目位', () => {
    const rows = emptyMappingRows()
    rows[0].subject_code = ' 1801 '
    rows[1].subject_code = '1802'
    rows[1].direction = 'credit'
    const payload = buildMappingsPayload({ lessee: ' 合成承租人甲 ', ledger: ' 合成账簿 ', rows })
    expect(payload).toEqual({
      lessee: '合成承租人甲',
      ledger: '合成账簿',
      items: [
        { subject_key: 'rou_asset', subject_code: '1801', direction: 'debit' },
        { subject_key: 'rou_accum_dep', subject_code: '1802', direction: 'credit' },
      ],
    })
  })

  it('缺承租人/账簿、重复科目、非法方向都拒绝提交', () => {
    const rows = emptyMappingRows()
    expect(() => buildMappingsPayload({ lessee: '', ledger: '合成账簿', rows })).toThrow('承租人')
    expect(() => buildMappingsPayload({ lessee: '合成承租人甲', ledger: '  ', rows })).toThrow('账簿')

    rows[0].subject_code = '1801'
    rows[3].subject_code = '1801'
    expect(() =>
      buildMappingsPayload({ lessee: '合成承租人甲', ledger: '合成账簿', rows }),
    ).toThrow('多个科目位')

    rows[3].subject_code = ''
    rows[0].direction = 'both'
    expect(() =>
      buildMappingsPayload({ lessee: '合成承租人甲', ledger: '合成账簿', rows }),
    ).toThrow('方向')
  })

  it('reconCheckBlockReason：承租人/账簿/期间缺一不可，期间必须 YYYY-MM', () => {
    expect(reconCheckBlockReason({ lessee: '', ledger: '合成账簿', period: '2026-03' })).toContain('承租人')
    expect(reconCheckBlockReason({ lessee: '甲', ledger: '', period: '2026-03' })).toContain('账簿')
    expect(reconCheckBlockReason({ lessee: '甲', ledger: '合成账簿', period: '' })).toContain('期间')
    expect(reconCheckBlockReason({ lessee: '甲', ledger: '合成账簿', period: '2026-13' })).toContain('期间')
    expect(reconCheckBlockReason({ lessee: '甲', ledger: '合成账簿', period: '2026-3' })).toContain('期间')
    expect(reconCheckBlockReason({ lessee: '甲', ledger: '合成账簿', period: '2026-03' })).toBe('')
  })
})

describe('结果整形：三态、来源与覆盖标记', () => {
  it('normalizeReconcile 保留后端计数与未知原因，不把未知当一致', () => {
    const view = normalizeReconcile(CHECK_RESULT)
    expect(view.counts).toEqual({ ok: 3, failed: 1, unknown: 1 })
    expect(view.meta).toMatchObject({ label: '差异', type: 'danger' })
    expect(view.unknownSubjects).toHaveLength(1)
    expect(view.unknownSubjects[0].reason).toContain('未配置')
    expect(view.failedSubjects[0].difference).toBe('-0.02')
    // 方向无关期末余额来源必须原样保留，供界面标注
    const liability = view.subjects.find((row) => row.subject_key === 'lease_liability')
    expect(liability.source).toBe('bip_closing_balance')
    expect(liability.source_label).toContain('期末余额')
  })

  it('counts 缺失时按行状态重算，不用假 0 掩盖', () => {
    const view = normalizeReconcile({ ...CHECK_RESULT, counts: null, status: 'unknown' })
    expect(view.counts).toEqual({ ok: 3, failed: 1, unknown: 1 })
    expect(view.meta.label).toBe('未知')
  })

  it('状态与覆盖标签：未知/过期/未覆盖都不是绿色', () => {
    expect(reconStatusMeta('ok').type).toBe('success')
    expect(reconStatusMeta('failed').type).toBe('danger')
    expect(reconStatusMeta('unknown').type).toBe('warning')
    expect(reconStatusMeta('whatever').type).toBe('warning')
    expect(coverageMeta({ selected: true })).toMatchObject({ label: '已采用', type: 'success' })
    expect(coverageMeta({ selected: false, stale: true }).type).toBe('danger')
    expect(coverageMeta({ selected: false, covers_period: false }).type).toBe('warning')
    expect(coverageMeta({ selected: false }).type).toBe('warning')
  })

  it('formatAmount：空值不补 0，两位小数展示', () => {
    expect(formatAmount(null)).toBe('—')
    expect(formatAmount(undefined)).toBe('—')
    expect(formatAmount('')).toBe('—')
    expect(formatAmount('570.5')).toBe('570.50')
    expect(formatAmount(282)).toBe('282.00')
    expect(formatAmount('-0.02')).toBe('-0.02')
    expect(formatAmount('not-a-number')).toBe('not-a-number')
  })
})

describe('ReconciliationPanel：上下文、映射保存与执行对账', () => {
  it('挂载即拉取上下文，未选范围时编辑器为五个空科目位', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    expect(ctx.mappingRows.value).toHaveLength(5)
    await fireMounted()
    expect(api.get).toHaveBeenCalledWith(`${API}/reconciliation/context`)
    expect(ctx.context.value.lessees).toEqual(['合成承租人甲', '合成承租人乙'])
    expect(ctx.context.value.periods).toEqual(['2026-03', '2026-06'])
    // 不自动替用户选择承租人/账簿（不猜）
    expect(ctx.form.lessee).toBe('')
    expect(ctx.form.ledger).toBe('')
  })

  it('选定承租人+账簿后按范围加载映射；未选全时清空且不发请求', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    await fireMounted()
    api.get.mockClear()

    ctx.form.lessee = '合成承租人甲'
    await ctx.loadMappings()
    expect(api.get).not.toHaveBeenCalled()
    expect(ctx.mappingRows.value.every((row) => row.subject_code === '')).toBe(true)

    api.get.mockResolvedValue({ data: { total: 3, items: MAPPINGS } })
    ctx.form.ledger = '合成账簿'
    await ctx.loadMappings()
    expect(api.get).toHaveBeenCalledWith(`${API}/reconciliation/mappings`, {
      params: { lessee: '合成承租人甲', ledger: '合成账簿' },
    })
    expect(ctx.mappingRows.value[0].subject_code).toBe('1801')
    expect(ctx.mappingRows.value[3].subject_code).toBe('')
  })

  it('保存映射提交整批科目位，成功提示只写本模块', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    await fireMounted()
    ctx.form.lessee = '合成承租人甲'
    ctx.form.ledger = '合成账簿'
    ctx.mappingRows.value[0].subject_code = '1801'

    api.put.mockResolvedValue({ data: { total: 1, items: [MAPPINGS[0]] } })
    await ctx.saveMappings()
    expect(api.put).toHaveBeenCalledWith(`${API}/reconciliation/mappings`, {
      lessee: '合成承租人甲',
      ledger: '合成账簿',
      items: [{ subject_key: 'rou_asset', subject_code: '1801', direction: 'debit' }],
    })
    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('未写 BIP'))
    expect(ctx.mappingRows.value[0].subject_code).toBe('1801')
  })

  it('范围为空时不发保存请求，只提示', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    await fireMounted()
    ctx.mappingRows.value[0].subject_code = '1801'
    await ctx.saveMappings()
    expect(api.put).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('承租人'))
  })

  it('保存失败（4xx）由拦截器提示，不重复弹业务错误也不丢编辑器内容', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    await fireMounted()
    ctx.form.lessee = '合成承租人甲'
    ctx.form.ledger = '合成账簿'
    ctx.mappingRows.value[0].subject_code = '1801'
    api.put.mockRejectedValue({ response: { status: 422, data: { detail: '科目位重复' } } })
    await ctx.saveMappings()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.mappingRows.value[0].subject_code).toBe('1801')
    expect(ctx.saving.value).toBe(false)
  })

  it('期间未选时不执行对账，不发 POST', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    await fireMounted()
    ctx.form.lessee = '合成承租人甲'
    ctx.form.ledger = '合成账簿'
    await ctx.runCheck()
    expect(api.post).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('期间'))
    expect(ctx.result.value).toBeNull()
  })

  it('执行对账提交修剪后的范围并整形结果', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    await fireMounted()
    ctx.form.lessee = ' 合成承租人甲 '
    ctx.form.ledger = ' 合成账簿 '
    ctx.form.period = '2026-03'
    api.post.mockResolvedValue({ data: CHECK_RESULT })
    await ctx.runCheck()
    expect(api.post).toHaveBeenCalledWith(`${API}/reconciliation/check`, {
      lessee: '合成承租人甲',
      ledger: '合成账簿',
      period: '2026-03',
    })
    const view = ctx.result.value
    expect(view.counts).toEqual({ ok: 3, failed: 1, unknown: 1 })
    expect(view.subjects).toHaveLength(5)
    expect(view.subjects[1].meta.label).toBe('差异')
    expect(view.unknownSubjects[0].subject_key).toBe('unrecognized_finance')
    expect(view.contracts).toHaveLength(2)
    expect(view.coverage.reasons).toHaveLength(1)
    expect(view.coverage.selected_count).toBe(1)
    expect(ctx.checking.value).toBe(false)
  })

  it('对账请求失败时保留空结果、不抛未处理异常', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    await fireMounted()
    ctx.form.lessee = '合成承租人甲'
    ctx.form.ledger = '合成账簿'
    ctx.form.period = '2026-03'
    api.post.mockRejectedValue(new Error('network'))
    await ctx.runCheck()
    expect(ctx.result.value).toBeNull()
    expect(ctx.checking.value).toBe(false)
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('修改映射后清空旧结果，避免拿旧差异继续展示', async () => {
    api.get.mockResolvedValue({ data: CONTEXT })
    const { ctx } = mountPanel(ReconciliationPanel)
    await fireMounted()
    ctx.result.value = normalizeReconcile(CHECK_RESULT)
    ctx.onMappingChanged()
    expect(ctx.result.value).toBeNull()
  })
})

describe('LeaseView：BIP对账页签独立于合同选择', () => {
  it('默认停在租赁合同页签，对账页签不需要先选合同', async () => {
    api.get.mockResolvedValue({ data: { total: 0, items: [] } })
    const { ctx } = mountPanel(LeaseView)
    expect(ctx.pageTab.value).toBe('contracts')
    ctx.pageTab.value = 'reconciliation'
    expect(ctx.selected.value).toBeNull()
    expect(ctx.pageTab.value).toBe('reconciliation')
  })

  it('页面挂载只请求合同与摘要，不会擅自触发对账请求', async () => {
    api.get.mockResolvedValue({ data: { total: 0, items: [] } })
    mountPanel(LeaseView)
    await fireMounted()
    const urls = api.get.mock.calls.map((call) => call[0])
    expect(urls).toContain(`${API}/contracts`)
    expect(urls).toContain(`${API}/summary`)
    expect(urls.some((url) => String(url).includes('reconciliation'))).toBe(false)
  })
})
