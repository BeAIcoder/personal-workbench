// 工会经费计提前端：批次台账、部门行编辑、计算预览与差异、确认只读、xlsx 导出。
// node 环境无 DOM：沿用 finance-lease / finance-recon 惯例，mock 生命周期、消息组件、
// api 与下载实现，直接驱动编译后的 setup()，只验证行为（状态与请求），不渲染模板。
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

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

const manifest = (await import('../src/modules/finance-union/manifest.js')).default
const apiModule = await import('../src/modules/finance-union/api.js')
const UnionView = (await import('../src/modules/finance-union/views/UnionView.vue')).default
const LinePanel = (await import('../src/modules/finance-union/views/LinePanel.vue')).default
const { ElMessage, ElMessageBox } = await import('element-plus')
const {
  SCOPE_NOTICE,
  batchStatusMeta,
  buildBatchPayload,
  buildLinesPayload,
  checkStateMeta,
  confirmBlockReason,
  exportFileNameFromResponse,
  formatAmount,
  formatRate,
  lineStatusMeta,
  linesToEditor,
  normalizeChecks,
  toAmount,
  unionApi,
} = apiModule

function mountPanel(Component, props = {}) {
  const events = []
  const ctx = Component.setup(props, {
    expose: () => {},
    emit: (name, payload) => events.push({ name, payload }),
  })
  return { ctx, events }
}
const mounted = (Component, props) => mountPanel(Component, props).ctx

async function tick() {
  await new Promise((resolve) => setTimeout(resolve, 0))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

async function fireMounted() {
  mountedCbs.splice(0).forEach((cb) => cb())
  await tick()
}

function routeGet(map) {
  api.get.mockImplementation((url) => {
    if (Object.prototype.hasOwnProperty.call(map, url)) return Promise.resolve({ data: map[url] })
    return Promise.reject(new Error(`未预期的 GET ${url}`))
  })
}

// ---------- 合成数据（无真实名称/金额） ----------

const BATCH = {
  id: 3,
  entity: '示例主体',
  period: '2026-11',
  rate: '0.020000',
  status: 'draft',
  status_label: '草稿',
  remark: '',
  line_count: 2,
  unknown_line_count: 0,
  current_accrued_total: '60.00',
  prior_variance_total: '100.00',
  current_book_amount_total: '160.00',
  confirmable: true,
  confirmed_at: null,
  created_at: '2026-11-20T10:00:00',
  updated_at: '2026-11-20T10:00:00',
}

const LINES = [
  {
    id: 1, batch_id: 3, department: '行政部',
    current_wage_base: '1000.00', prior_accrual: '500.00', prior_actual: '480.00',
    line_status: 'ok', line_status_label: '可计算',
    current_accrued: '20.00', prior_variance: '-20.00', current_book_amount: '0.00',
    missing_fields: [], note: '上期为负数差异，本期入账冲回',
  },
  {
    id: 2, batch_id: 3, department: '技术部',
    current_wage_base: '2000.00', prior_accrual: '300.00', prior_actual: '420.00',
    line_status: 'ok', line_status_label: '可计算',
    current_accrued: '40.00', prior_variance: '120.00', current_book_amount: '160.00',
    missing_fields: [], note: '',
  },
]

const TOTALS = {
  current_wage_base: '3000.00', current_accrued: '60.00',
  prior_accrual: '800.00', prior_actual: '900.00',
  prior_variance: '100.00', current_book_amount: '160.00',
}

const CHECKS = [
  { key: 'batch_lines_present', title: '批次至少一条部门行', status: 'ok', expected: '1', actual: '2', difference: '0', message: '共 2 条部门行' },
  { key: 'line_inputs_complete', title: '工资基数与上期数已齐全', status: 'ok', expected: '2', actual: '2', difference: '0', message: '全部部门行三数齐全' },
]

const PREVIEW = {
  batch_id: 3, entity: '示例主体', period: '2026-11', rate: '0.020000',
  status: 'draft', status_label: '草稿', rule_version: 'union-v1',
  line_count: 2, ok_line_count: 2, unknown_line_count: 0,
  lines: LINES.map((line) => ({ ...line })),
  totals: TOTALS, checks: CHECKS,
  check_counts: { ok: 2, failed: 0, unknown: 0 },
  all_passed: true, has_unknown: false, confirmable: true, confirm_reason: '',
}

const BATCH_DETAIL = {
  batch: BATCH,
  lines: LINES.map((line) => ({ ...line })),
  totals: TOTALS,
  checks: CHECKS,
  check_counts: { ok: 2, failed: 0, unknown: 0 },
  all_passed: true,
  has_unknown: false,
  confirmable: true,
  confirm_reason: '',
  rule_version: 'union-v1',
}

const LISTING = { total: 1, page: 1, page_size: 200, items: [BATCH] }
const SUMMARY = {
  total_batches: 1, draft_count: 1, confirmed_count: 0, unknown_line_total: 0,
  current_accrued_total: '60.00', prior_variance_total: '100.00',
  current_book_amount_total: '160.00', batches: [],
}

const LINE_ROUTES = {
  '/finance/union/batches/3/lines': LINES,
  '/finance/union/batches/3/preview': PREVIEW,
}

beforeEach(() => {
  vi.clearAllMocks()
  mountedCbs.splice(0)
  api.get.mockResolvedValue({ data: { items: [], batches: [] } })
  saveBlob.mockReturnValue(true)
})

// ---------- 菜单与契约 ----------

describe('finance-union manifest', () => {
  it('菜单挂在 财务 → 账务处理，order 100，使用已注册图标', () => {
    expect(manifest.name).toBe('finance-union')
    expect(manifest.title).toBe('工会经费计提')
    expect(manifest.routes).toHaveLength(1)
    expect(manifest.routes[0].path).toBe('/finance/union')
    expect(manifest.routes[0].name).toBe('finance-union')
    expect(manifest.menu).toMatchObject({
      group: '财务',
      subgroup: '账务处理',
      title: '工会经费计提',
      icon: 'Coin',
      path: '/finance/union',
      order: 100,
    })
  })

  it('api 封装全部走 /finance/union 前缀（baseURL 已含 /api）', () => {
    const calls = [
      [() => unionApi.batches(), '/finance/union/batches'],
      [() => unionApi.batch(3), '/finance/union/batches/3'],
      [() => unionApi.lines(3), '/finance/union/batches/3/lines'],
      [() => unionApi.preview(3), '/finance/union/batches/3/preview'],
      [() => unionApi.checks(3), '/finance/union/batches/3/checks'],
      [() => unionApi.summary(), '/finance/union/summary'],
    ]
    for (const [run, url] of calls) {
      run()
      expect(api.get.mock.calls.at(-1)[0]).toBe(url)
    }
    void unionApi.confirm(3)
    expect(api.post).toHaveBeenLastCalledWith('/finance/union/batches/3/confirm')
  })

  it('口径声明覆盖三条边界（手工基数 / 无 HR / 无 60-40 与扣除判断）', () => {
    expect(SCOPE_NOTICE).toContain('手工录入')
    expect(SCOPE_NOTICE).toContain('不从 HR 取数')
    expect(SCOPE_NOTICE).toContain('60/40')
    expect(SCOPE_NOTICE).toContain('税前扣除')
  })
})

// ---------- 请求体与展示映射 ----------

describe('api 纯函数', () => {
  it('批次请求体：费率必填且按 6 位小数下发，不提供默认值', () => {
    const payload = buildBatchPayload({ entity: ' 示例主体 ', period: '2026-11', rate: 0.02, remark: ' 备注 ' })
    expect(payload).toEqual({ entity: '示例主体', period: '2026-11', rate: '0.020000', remark: '备注' })
    expect(() => buildBatchPayload({ entity: '示例主体', period: '2026-11', rate: null })).toThrow(/计提率必须显式填写/)
    expect(() => buildBatchPayload({ entity: '示例主体', period: '2026-11' })).toThrow(/计提率/)
    expect(() => buildBatchPayload({ entity: '示例主体', period: '2026-11', rate: 2 })).toThrow(/0~1/)
    expect(() => buildBatchPayload({ entity: '示例主体', period: '2026-11', rate: 0 })).toThrow(/0~1/)
    expect(() => buildBatchPayload({ entity: '  ', period: '2026-11', rate: 0.02 })).toThrow(/主体/)
    expect(() => buildBatchPayload({ entity: '示例主体', period: '2026-13', rate: 0.02 })).toThrow(/YYYY-MM/)
    expect(() => buildBatchPayload({ entity: '示例主体', period: '2026/11', rate: 0.02 })).toThrow(/YYYY-MM/)
  })

  it('空输入 = 未录入（null），显式 0 保留为 0.00', () => {
    expect(toAmount('')).toBeNull()
    expect(toAmount(null)).toBeNull()
    expect(toAmount(undefined)).toBeNull()
    expect(toAmount(0)).toBe('0.00')
    expect(toAmount('0')).toBe('0.00')
    expect(toAmount('1234.5')).toBe('1234.50')
    expect(formatAmount(null)).toBe('—')
    expect(formatAmount('0.00')).toBe('0.00')
    expect(formatAmount('-20')).toBe('-20.00')
  })

  it('行请求体：整行空白被过滤，缺部门/负数/重复部门当场拒绝', () => {
    expect(buildLinesPayload([{ department: '', current_wage_base: null, prior_accrual: null, prior_actual: null }]))
      .toEqual({ items: [] })
    const payload = buildLinesPayload([
      { department: ' 行政部 ', current_wage_base: 1000, prior_accrual: 0, prior_actual: null },
      { department: '技术部', current_wage_base: 2000, prior_accrual: 300, prior_actual: 420 },
    ])
    expect(payload.items[0]).toEqual({
      department: '行政部', current_wage_base: '1000.00', prior_accrual: '0.00', prior_actual: null,
    })
    expect(payload.items[1].prior_actual).toBe('420.00')
    expect(() => buildLinesPayload([{ department: '', current_wage_base: 1 }])).toThrow(/缺少部门/)
    expect(() => buildLinesPayload([{ department: '行政部', current_wage_base: -1 }])).toThrow(/负数/)
    expect(() =>
      buildLinesPayload([
        { department: '行政部', current_wage_base: 1 },
        { department: '行政部', current_wage_base: 2 },
      ]),
    ).toThrow(/重复/)
  })

  it('状态映射：未知状态一律非绿色', () => {
    expect(batchStatusMeta('draft').label).toBe('草稿')
    expect(batchStatusMeta('confirmed').type).toBe('success')
    expect(batchStatusMeta('archived').type).toBe('warning')
    expect(lineStatusMeta('ok').label).toBe('可计算')
    expect(lineStatusMeta('unknown').type).toBe('warning')
    expect(lineStatusMeta(undefined).type).toBe('warning')
    expect(checkStateMeta('failed').type).toBe('danger')
    expect(checkStateMeta('unknown').type).toBe('warning')
    expect(formatRate('0.02')).toBe('2.00%')
    expect(formatRate(null)).toBe('—')
  })

  it('confirmBlockReason 覆盖未选/已确认/缺数据/后端原因', () => {
    expect(confirmBlockReason(null)).toContain('请先选择')
    expect(confirmBlockReason({ ...PREVIEW, status: 'confirmed' })).toContain('已确认')
    expect(confirmBlockReason({
      ...PREVIEW, confirmable: false, unknown_line_count: 2, confirm_reason: '存在缺数据的部门行（市场部）',
    })).toContain('缺数据')
    expect(confirmBlockReason({ ...PREVIEW, confirmable: false, confirm_reason: '批次没有任何部门行，无法确认' }))
      .toContain('没有任何部门行')
    expect(confirmBlockReason({
      ...PREVIEW, confirmable: true, unknown_line_count: 3,
    })).toContain('缺数据')
    expect(confirmBlockReason(PREVIEW)).toBe('')
  })

  it('校验归一化：通过/差异/未知三态，空结果不算通过', () => {
    expect(normalizeChecks({ ...PREVIEW }).state).toBe('ok')
    expect(normalizeChecks({
      check_counts: { ok: 1, failed: 1, unknown: 0 }, all_passed: false,
      checks: [{ status: 'failed' }],
    }).state).toBe('failed')
    expect(normalizeChecks({
      check_counts: { ok: 0, failed: 0, unknown: 1 }, all_passed: false,
      checks: [{ status: 'unknown' }],
    }).state).toBe('unknown')
    expect(normalizeChecks({ checks: [] }).state).toBe('unknown')
  })

  it('linesToEditor 保留 null 与派生值', () => {
    const rows = linesToEditor([
      { department: '市场部', current_wage_base: '1500.00', prior_accrual: '0.00', prior_actual: null,
        line_status: 'unknown', line_status_label: '缺数据', current_accrued: null,
        prior_variance: null, current_book_amount: null, missing_fields: ['prior_actual'] },
    ])
    expect(rows[0].prior_actual).toBeNull()
    expect(rows[0].prior_accrual).toBe(0)
    expect(rows[0].current_accrued).toBeNull()
    expect(rows[0].missing_fields).toEqual(['prior_actual'])
  })

  it('导出文件名优先取 Content-Disposition 中文名，缺失时本地兜底', () => {
    const chinese = 'attachment; filename="union_x.xlsx"; filename*=UTF-8\'\'%E5%B7%A5%E4%BC%9A.xlsx'
    expect(exportFileNameFromResponse({ headers: { 'content-disposition': chinese } }, BATCH)).toBe('工会.xlsx')
    expect(exportFileNameFromResponse({ headers: {} }, BATCH)).toBe('示例主体_工会经费计提_2026-11.xlsx')
  })
})

// ---------- 批次台账 ----------

describe('UnionView：批次台账', () => {
  it('挂载即拉批次与摘要，空态不报错', async () => {
    routeGet({ '/finance/union/batches': LISTING, '/finance/union/summary': SUMMARY })
    const ctx = mounted(UnionView)
    expect(mountedCbs).toHaveLength(1)
    await fireMounted()
    expect(ctx.batches.value).toHaveLength(1)
    expect(ctx.summary.value.confirmed_count).toBe(0)
    expect(ctx.selected.value).toBeNull()
  })

  it('新建批次：表单不预填费率，空费率被前端拦下且不发请求', async () => {
    const ctx = mounted(UnionView)
    ctx.openCreate()
    expect(ctx.dialogVisible.value).toBe(true)
    expect(ctx.form.rate).toBeNull()
    ctx.form.entity = '示例主体'
    ctx.form.period = '2026-11'
    await ctx.submitBatch()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('计提率'))
    expect(api.post).not.toHaveBeenCalled()
  })

  it('新建成功：POST 固定 schema 并刷新台账', async () => {
    routeGet({
      '/finance/union/batches': LISTING,
      '/finance/union/summary': SUMMARY,
      '/finance/union/batches/3': BATCH_DETAIL,
    })
    api.post.mockResolvedValueOnce({ data: BATCH })
    const ctx = mounted(UnionView)
    ctx.openCreate()
    Object.assign(ctx.form, { entity: '示例主体', period: '2026-11', rate: 0.02, remark: '' })
    await ctx.submitBatch()
    expect(api.post).toHaveBeenCalledWith('/finance/union/batches', {
      entity: '示例主体', period: '2026-11', rate: '0.020000', remark: '',
    })
    expect(ElMessage.success).toHaveBeenCalled()
    expect(ctx.dialogVisible.value).toBe(false)
    expect(ctx.selected.value.id).toBe(3)
  })

  it('主体+期间重复（409）：弹窗保持打开，交由拦截器提示', async () => {
    const ctx = mounted(UnionView)
    ctx.openCreate()
    Object.assign(ctx.form, { entity: '示例主体', period: '2026-11', rate: 0.02 })
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: '已存在批次（id=3）' } } })
    await ctx.submitBatch()
    expect(ctx.dialogVisible.value).toBe(true)
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('编辑草稿走 PATCH；已确认批次不可编辑、不可删除', async () => {
    const ctx = mounted(UnionView)
    ctx.openEdit(BATCH)
    expect(ctx.editingId.value).toBe(3)
    expect(ctx.form.rate).toBeCloseTo(0.02)
    api.patch.mockResolvedValueOnce({ data: BATCH })
    ctx.form.remark = ' 调整说明 '
    await ctx.submitBatch()
    expect(api.patch).toHaveBeenCalledWith('/finance/union/batches/3', {
      entity: '示例主体', period: '2026-11', rate: '0.020000', remark: '调整说明',
    })

    const confirmed = { ...BATCH, status: 'confirmed', status_label: '已确认' }
    ctx.openEdit(confirmed)
    expect(ctx.editingId.value).toBeNull() // 已确认批次不进入编辑态
    await ctx.removeBatch(confirmed)
    expect(api.delete).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('已确认'))
  })

  it('删除草稿批次：确认后调用 DELETE 并清空选中', async () => {
    routeGet({ '/finance/union/batches': LISTING, '/finance/union/summary': SUMMARY })
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    api.delete.mockResolvedValueOnce({ data: { deleted: true } })
    const ctx = mounted(UnionView)
    ctx.selected.value = BATCH
    await ctx.removeBatch(BATCH)
    expect(api.delete).toHaveBeenCalledWith('/finance/union/batches/3')
    expect(ctx.selected.value).toBeNull()
  })

  it('删除取消不发请求', async () => {
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')
    const ctx = mounted(UnionView)
    await ctx.removeBatch(BATCH)
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('选中批次拉取详情', async () => {
    routeGet({ '/finance/union/batches/3': BATCH_DETAIL })
    const ctx = mounted(UnionView)
    await ctx.selectBatch(BATCH)
    expect(ctx.selected.value.id).toBe(3)
    expect(ctx.selected.value.entity).toBe('示例主体')
  })
})

// ---------- 部门行 / 预览 / 确认 / 导出 ----------

describe('LinePanel：部门行与计算预览', () => {
  it('挂载拉取行与预览，缺数据行按 unknown 展示', async () => {
    routeGet(LINE_ROUTES)
    const ctx = mounted(LinePanel, { batch: BATCH })
    await fireMounted()
    expect(api.get).toHaveBeenCalledWith('/finance/union/batches/3/lines')
    expect(api.get).toHaveBeenCalledWith('/finance/union/batches/3/preview')
    expect(ctx.rows.value).toHaveLength(2)
    expect(ctx.rows.value[0].current_accrued).toBe('20.00')
    expect(ctx.rows.value[0].prior_variance).toBe('-20.00')

    vi.clearAllMocks()
    routeGet({
      '/finance/union/batches/3/lines': [{
        department: '市场部', current_wage_base: '1500.00', prior_accrual: '0.00', prior_actual: null,
        line_status: 'unknown', line_status_label: '缺数据', current_accrued: null,
        prior_variance: null, current_book_amount: null, missing_fields: ['prior_actual'], note: '缺 上期实际数',
      }],
      '/finance/union/batches/3/preview': {
        ...PREVIEW, confirmable: false, unknown_line_count: 1,
        confirm_reason: '存在缺数据的部门行（市场部），补齐工资基数与上期数后才能确认',
      },
    })
    const bad = mounted(LinePanel, { batch: BATCH })
    await bad.reload()
    expect(bad.rows.value[0].line_status).toBe('unknown')
    expect(bad.rows.value[0].current_book_amount).toBeNull()
    expect(bad.canConfirm.value).toBe(false)
    expect(bad.blockReason.value).toContain('缺数据')
  })

  it('保存部门行：显式 0 保持 0.00，空值提交 null', async () => {
    routeGet(LINE_ROUTES)
    api.put.mockResolvedValueOnce({ data: LINES })
    const { ctx, events } = mountPanel(LinePanel, { batch: BATCH })
    ctx.rows.value = [
      { department: '行政部', current_wage_base: 1000, prior_accrual: 0, prior_actual: null },
      { department: '技术部', current_wage_base: 2000, prior_accrual: 300, prior_actual: 420 },
    ]
    await ctx.saveLines()
    expect(api.put).toHaveBeenCalledWith('/finance/union/batches/3/lines', {
      items: [
        { department: '行政部', current_wage_base: '1000.00', prior_accrual: '0.00', prior_actual: null },
        { department: '技术部', current_wage_base: '2000.00', prior_accrual: '300.00', prior_actual: '420.00' },
      ],
    })
    expect(events.some((e) => e.name === 'changed')).toBe(true)
    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('部门行'))
  })

  it('部门重复在提交前被拦下，不发请求', async () => {
    const ctx = mounted(LinePanel, { batch: BATCH })
    ctx.rows.value = [
      { department: '行政部', current_wage_base: 1 },
      { department: '行政部', current_wage_base: 2 },
    ]
    await ctx.saveLines()
    expect(api.put).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('重复'))
  })

  it('确认：不可确认时只提示不发请求；可确认时 POST 并通知父组件', async () => {
    routeGet(LINE_ROUTES)
    const { ctx, events } = mountPanel(LinePanel, { batch: BATCH })
    ctx.preview.value = { ...PREVIEW, confirmable: false, unknown_line_count: 1, confirm_reason: '存在缺数据' }
    await ctx.confirmBatch()
    expect(api.post).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('缺数据'))

    ctx.preview.value = { ...PREVIEW }
    api.post.mockResolvedValueOnce({ data: { ...BATCH, status: 'confirmed' } })
    await ctx.confirmBatch()
    expect(api.post).toHaveBeenCalledWith('/finance/union/batches/3/confirm')
    expect(events.some((e) => e.name === 'changed')).toBe(true)
  })

  it('确认 409 时交给拦截器，不本地重复弹错也不改状态', async () => {
    const ctx = mounted(LinePanel, { batch: BATCH })
    ctx.preview.value = { ...PREVIEW }
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: '该批次已确认' } } })
    await ctx.confirmBatch()
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('已确认批次只读：保存按钮禁用、blockReason 说明原因', async () => {
    routeGet({
      '/finance/union/batches/3/lines': LINES,
      '/finance/union/batches/3/preview': { ...PREVIEW, status: 'confirmed', status_label: '已确认', confirmable: false, confirm_reason: '' },
    })
    const confirmedBatch = { ...BATCH, status: 'confirmed', status_label: '已确认' }
    const ctx = mounted(LinePanel, { batch: confirmedBatch })
    await ctx.reload()
    expect(ctx.readonly.value).toBe(true)
    expect(ctx.canConfirm.value).toBe(false)
    expect(ctx.blockReason.value).toContain('已确认')
  })

  it('导出 xlsx：用 Content-Disposition 中文名调用 saveBlob', async () => {
    const disposition = 'attachment; filename="union_x.xlsx"; filename*=UTF-8\'\'%E5%B7%A5%E4%BC%9A%E7%BB%8F%E8%B4%B9.xlsx'
    api.get.mockResolvedValueOnce({ data: new Blob(['x']), headers: { 'content-disposition': disposition } })
    const ctx = mounted(LinePanel, { batch: BATCH })
    await ctx.exportXlsx()
    expect(api.get).toHaveBeenCalledWith('/finance/union/batches/3/export.xlsx', { responseType: 'blob' })
    expect(saveBlob).toHaveBeenCalledWith(expect.anything(), '工会经费.xlsx')
  })

  it('导出失败不落盘、不假成功', async () => {
    api.get.mockRejectedValueOnce({ response: { status: 500 } })
    const ctx = mounted(LinePanel, { batch: BATCH })
    await ctx.exportXlsx()
    expect(saveBlob).not.toHaveBeenCalled()
    expect(ctx.exporting.value).toBe(false)
  })

  it('行增删保持显式空值', () => {
    const ctx = mounted(LinePanel, { batch: BATCH })
    ctx.rows.value = []
    ctx.addRow()
    expect(ctx.rows.value).toHaveLength(1)
    expect(ctx.rows.value[0].current_wage_base).toBeNull()
    ctx.removeRow(0)
    expect(ctx.rows.value).toHaveLength(0)
  })

  it('无批次时不发任何请求（空态守卫）', async () => {
    const ctx = mounted(LinePanel, { batch: null })
    await ctx.reload()
    await ctx.saveLines()
    await ctx.confirmBatch()
    expect(api.get).not.toHaveBeenCalled()
    expect(api.put).not.toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()
  })
})
