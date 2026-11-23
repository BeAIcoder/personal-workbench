// 商户核销对账前端：批次/四源上传/合同范围勾选/口径参数/勾稽结果/匹配与问题复核/三格式导出。
// node 环境无 DOM：沿用 finance-receipt 惯例，mock 生命周期、消息组件、api 与下载实现，
// 直接驱动编译后的 setup()，只验证行为（状态与请求），不渲染模板。
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

const manifestModule = await import('../src/modules/finance-recon/manifest.js')
const reconApiModule = await import('../src/modules/finance-recon/api.js')
const ReconView = (await import('../src/modules/finance-recon/views/ReconView.vue')).default
const SourcePanel = (await import('../src/modules/finance-recon/views/SourcePanel.vue')).default
const ResultPanel = (await import('../src/modules/finance-recon/views/ResultPanel.vue')).default
const ReviewPanel = (await import('../src/modules/finance-recon/views/ReviewPanel.vue')).default
const { ElMessage, ElMessageBox } = await import('element-plus')
const { reconApi, checkStateMeta, issueStatusMeta, exportFileName } = reconApiModule
const { helpEntryByPath } = await import('../src/utils/helpIndex')

function mountSource(props = {}) {
  return SourcePanel.setup({ batch: null, ...props }, { expose: () => {}, emit: () => {} })
}
function mountResult(props = {}) {
  return ResultPanel.setup({ batch: null, ...props }, { expose: () => {}, emit: () => {} })
}
function mountReview(props = {}) {
  return ReviewPanel.setup({ batch: null, ...props }, { expose: () => {}, emit: () => {} })
}

// 触发已捕获的 onMounted 回调并等待其内部请求链落定
async function fireMounted() {
  const cbs = mountedCbs.splice(0)
  cbs.forEach((cb) => cb())
  await new Promise((resolve) => setTimeout(resolve, 0))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

const BATCH = {
  id: 7,
  name: '11月商户核销对账',
  object_tag: '示例商户',
  as_of: '2026-11-18',
  tolerance: '0.01',
  status: 'draft',
  status_label: '草稿',
  needs_recheck: false,
  ever_ready: false,
  error_summary: '',
  contract_scope: ['HT-SYN-001'],
  counts: { sources: 0, records: 0, matches: 0, issues: 0, terms: 0 },
  created_at: '2026-11-18T10:00:00',
  updated_at: '2026-11-18T10:00:00',
}

const SOURCES = {
  total: 2,
  items: [
    {
      id: 1, kind: 'writeoff', kind_label: '核销', original_name: '核销明细样本.xlsx',
      parse_status: 'parsed', row_count: 2, error: '', sha256: 'a'.repeat(64),
      created_at: '2026-11-18T10:00:00', contracts: ['HT-SYN-001', 'HT-SYN-002'],
    },
    {
      id: 2, kind: 'bill', kind_label: '账单', original_name: '账单通知单样本.pdf',
      parse_status: 'failed', row_count: 0, error: '账单缺文本层：第 1 页', sha256: 'b'.repeat(64),
      created_at: '2026-11-18T10:01:00', contracts: [],
    },
  ],
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
  it('路由 /finance/recon 指向 ReconView，菜单在财务/账务处理', () => {
    const routes = manifestModule.default.routes
    expect(routes).toHaveLength(1)
    expect(routes[0].path).toBe('/finance/recon')
    expect(typeof routes[0].component).toBe('function')
    expect(manifestModule.default.menu).toMatchObject({
      group: '财务',
      subgroup: '账务处理',
      title: '商户核销对账',
      path: '/finance/recon',
    })
  })

  it('reconApi 路径不带 /api 前缀（baseURL 已含 /api，不重复拼接）', () => {
    expect(api.defaults.baseURL).toBe('/api')
    api.get.mockResolvedValueOnce({ data: { items: [] } })
    void reconApi.batches()
    expect(api.get.mock.calls[0][0]).toBe('/finance/recon/batches')
    api.get.mockResolvedValueOnce({ data: { items: [] } })
    void reconApi.sources(7)
    expect(api.get.mock.calls[1][0]).toBe('/finance/recon/batches/7/sources')
  })

  it('帮助索引有 /finance/recon 条目（§6.17）', () => {
    const entry = helpEntryByPath('/finance/recon')
    expect(entry).toBeTruthy()
    expect(entry.section).toBe('6.17')
    expect(entry.title).toBe('商户核销对账')
  })
})

describe('状态展示：未知/待定绝不用绿色通过', () => {
  it('勾稽结论映射：pass 绿色、diff/unknown/info 非绿', () => {
    expect(checkStateMeta('pass')).toMatchObject({ label: '通过', type: 'success' })
    expect(checkStateMeta('diff').type).not.toBe('success')
    expect(checkStateMeta('unknown')).toMatchObject({ label: '待定', type: 'warning' })
    expect(checkStateMeta('info').type).not.toBe('success')
  })

  it('问题待确认是警告色、已排除非绿色', () => {
    expect(issueStatusMeta('pending').type).toBe('warning')
    expect(issueStatusMeta('dismissed').type).not.toBe('success')
    expect(issueStatusMeta('confirmed').type).toBe('success')
  })

  it('导出文件名含批次名与对账日且不含路径非法字符', () => {
    expect(exportFileName({ name: '11月核销对账', as_of: '2026-11-18' }, 'xlsx')).toBe(
      '11月核销对账_商户核销对账_2026-11-18.xlsx',
    )
    const weird = exportFileName({ name: 'A/B\\C:D', as_of: '2026-11-18' }, 'md')
    expect(weird).not.toMatch(/[/\\:]/)
    expect(weird.endsWith('.md')).toBe(true)
  })
})

describe('ReconView：批次列表、创建与选择', () => {
  it('loadBatches 拉取批次列表并保留顺序', async () => {
    api.get.mockResolvedValueOnce({ data: { total: 1, items: [BATCH] } })
    const ctx = ReconView.setup({}, { expose: () => {}, emit: () => {} })
    await ctx.loadBatches()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches', { params: { limit: 50 } })
    expect(ctx.batches.value).toEqual([BATCH])
  })

  it('createBatch 提交名称/对象/对账日/容差并刷新选中', async () => {
    api.post.mockResolvedValueOnce({ data: { ...BATCH, id: 9 } })
    api.get.mockImplementation((url) => {
      if (url === '/finance/recon/batches') return Promise.resolve({ data: { total: 2, items: [{ ...BATCH, id: 9 }] } })
      return Promise.resolve({ data: { ...BATCH, id: 9 } })
    })
    const ctx = ReconView.setup({}, { expose: () => {}, emit: () => {} })
    ctx.form.name = '新批次'
    ctx.form.object_tag = '对象乙'
    ctx.form.as_of = '2026-11-19'
    ctx.form.tolerance = 0.01
    await ctx.createBatch()
    expect(api.post).toHaveBeenCalledWith('/finance/recon/batches', {
      name: '新批次',
      object_tag: '对象乙',
      as_of: '2026-11-19',
      tolerance: '0.01',
      contract_scope: [],
    })
    expect(ctx.batch.value.id).toBe(9)
  })

  it('selectBatch 拉取详情并带入 contract_scope；未选择批次时面板为空态', async () => {
    api.get.mockResolvedValueOnce({ data: BATCH })
    const ctx = ReconView.setup({}, { expose: () => {}, emit: () => {} })
    await ctx.selectBatch(7)
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7')
    expect(ctx.batch.value).toEqual(BATCH)
    expect(ctx.batch.value.contract_scope).toEqual(['HT-SYN-001'])
    // 空态：无批次时 SourcePanel 载入被守卫（不发起源列表请求）
    const src = mountSource({ batch: null })
    await src.loadSources()
    expect(api.get).not.toHaveBeenCalledWith('/finance/recon/batches/7/sources')
    expect(src.sources.value).toEqual([])
  })

  it('needs_recheck 有清晰提示文案；ready 批次禁用资料编辑', () => {
    const ctx = ReconView.setup({}, { expose: () => {}, emit: () => {} })
    ctx.batch.value = { ...BATCH, needs_recheck: true, status: 'ready' }
    expect(ctx.needsRecheck.value).toBe(true)
    expect(ctx.recheckHint).toContain('重新执行勾稽检查')
    const src = mountSource({ batch: ctx.batch.value })
    expect(src.canEdit.value).toBe(false)
    const draft = mountSource({ batch: BATCH })
    expect(draft.canEdit.value).toBe(true)
  })
})

describe('SourcePanel：上传、源列表、失败原因与删除', () => {
  it('loadSources 拉取源列表并合并检测合同（scope 勾选依据）', async () => {
    api.get.mockResolvedValueOnce({ data: SOURCES })
    const ctx = mountSource({ batch: BATCH })
    await ctx.loadSources()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/sources')
    expect(ctx.sources.value).toHaveLength(2)
    expect(ctx.detectedContracts.value).toEqual(['HT-SYN-001', 'HT-SYN-002'])
    // 已勾选范围保留在勾选状态里
    expect(ctx.scopeChecked.value).toEqual(['HT-SYN-001'])
  })

  it('onFileChange 取值、onFileRemove 复位（limit=1 换文件先移除）', () => {
    const ctx = mountSource({ batch: BATCH })
    ctx.onFileChange({ raw: { name: 'a.xlsx' } })
    expect(ctx.uploadFile.value).toEqual({ name: 'a.xlsx' })
    ctx.onFileRemove()
    expect(ctx.uploadFile.value).toBeNull()
  })

  it('submitUpload 按 kind 上传 FormData，成功后刷新列表并复位文件', async () => {
    api.post.mockResolvedValueOnce({ data: { source_id: 5, skipped_duplicates: 0 } })
    api.get.mockResolvedValueOnce({ data: SOURCES })
    const ctx = mountSource({ batch: BATCH })
    ctx.openUpload('claim')
    ctx.onFileChange({ raw: { name: '认领.xlsx' } })
    await ctx.submitUpload()
    const [url, fd, options] = api.post.mock.calls[0]
    expect(url).toBe('/finance/recon/batches/7/sources/claim')
    expect(fd).toBeInstanceOf(FormData)
    expect(options).toMatchObject({ headers: { 'Content-Type': 'multipart/form-data' } })
    expect(ctx.uploadFile.value).toBeNull()
    expect(ctx.uploadDialog.value).toBe(false)
  })

  it('结构错误（400）走统一提示：catch 不重复弹错、批次保持可选', async () => {
    api.post.mockRejectedValueOnce({ response: { status: 400, data: { detail: '文本金额非法：12.3元' } } })
    const ctx = mountSource({ batch: BATCH })
    ctx.openUpload('writeoff')
    ctx.onFileChange({ raw: { name: '坏样本.xlsx' } })
    await ctx.submitUpload()
    expect(ElMessage.error).not.toHaveBeenCalled() // 业务 catch 不重复弹错（拦截器已提示）
    expect(ctx.uploading.value).toBe(false)
    expect(ctx.uploadDialog.value).toBe(true) // 失败保留弹窗与已选 kind，便于修正重试
  })

  it('deleteSourceRow 调用删除并刷新源列表', async () => {
    api.delete.mockResolvedValueOnce({ data: { ok: true } })
    api.get.mockResolvedValueOnce({ data: { total: 0, items: [] } })
    const ctx = mountSource({ batch: BATCH })
    await ctx.deleteSourceRow(SOURCES.items[0])
    expect(api.delete).toHaveBeenCalledWith('/finance/recon/batches/7/sources/1')
    expect(ctx.sources.value).toEqual([])
  })
})

describe('SourcePanel：合同范围勾选与口径参数', () => {
  it('saveScope PATCH contract_scope（去空、保序）', async () => {
    api.get.mockResolvedValueOnce({ data: SOURCES })
    api.patch.mockResolvedValueOnce({ data: { ...BATCH, contract_scope: ['HT-SYN-001', 'HT-SYN-002'] } })
    const ctx = mountSource({ batch: BATCH })
    await ctx.loadSources()
    ctx.scopeChecked.value = ['HT-SYN-002', 'HT-SYN-001', '']
    await ctx.saveScope()
    expect(api.patch).toHaveBeenCalledWith('/finance/recon/batches/7', {
      contract_scope: ['HT-SYN-002', 'HT-SYN-001'],
    })
  })

  it('terms=null 显示未录入并给出缺失项', async () => {
    api.get.mockResolvedValueOnce({
      data: { batch_id: 7, items: [{ contract_no: 'HT-SYN-001', terms: null, complete: false, missing: ['未录入口径参数'] }], out_of_scope_items: [] },
    })
    const ctx = mountSource({ batch: BATCH })
    await ctx.loadTerms()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/terms')
    expect(ctx.terms.value[0].terms).toBeNull()
    expect(ctx.missingText(ctx.terms.value[0])).toBe('未录入口径参数')
  })

  it('保存口径参数走固定 schema（billing_method/重构截止/月费/违约金），不发自由 JSON', async () => {
    api.put.mockResolvedValueOnce({ data: { contract_no: 'HT-SYN-001', terms: {}, complete: true, missing: [] } })
    const ctx = mountSource({ batch: BATCH })
    ctx.openTerms({ contract_no: 'HT-SYN-001', terms: null, complete: false, missing: [] })
    ctx.editor.contract_start = '2026-01-01'
    ctx.editor.contract_end = '2026-12-31'
    ctx.editor.billing_method = 'whole_month'
    ctx.editor.reconstruction_cutoff = 'through_as_of'
    ctx.editor.monthly_fees = [{ fee_type: '固定租金', monthly_amount: 1000 }]
    ctx.editor.due_rule = 'expense_month_day'
    ctx.editor.due_day = 20
    ctx.editor.penalty.rate_per_day = 0.001
    ctx.editor.penalty.start_rule = 'due_date'
    ctx.editor.penalty.cap = 5000
    await ctx.saveTerms()
    expect(api.put).toHaveBeenCalledWith('/finance/recon/batches/7/terms', {
      contract_no: 'HT-SYN-001',
      terms: {
        contract_start: '2026-01-01',
        contract_end: '2026-12-31',
        billing_method: 'whole_month',
        reconstruction_cutoff: 'through_as_of',
        monthly_fees: [{ fee_type: '固定租金', monthly_amount: '1000.00' }],
        custom_lines: [],
        additional_charges: [],
        credits: [],
        due_rule: 'expense_month_day',
        due_day: 20,
        penalty: { rate_per_day: '0.00100000', start_rule: 'due_date', cap: '5000.00', waived: false },
      },
    })
  })

  it('重构截止口径未选择时禁止保存（必填，不默认）', async () => {
    const ctx = mountSource({ batch: BATCH })
    ctx.openTerms({ contract_no: 'HT-SYN-001', terms: null, complete: false, missing: [] })
    ctx.editor.billing_method = 'whole_month'
    ctx.editor.reconstruction_cutoff = null
    await ctx.saveTerms()
    expect(api.put).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
  })

  it('custom_lines 计费方式只要求自定义明细（缺项给出中文原因）', async () => {
    const ctx = mountSource({ batch: BATCH })
    ctx.openTerms({ contract_no: 'HT-SYN-001', terms: null, complete: false, missing: [] })
    ctx.editor.billing_method = 'custom_lines'
    ctx.editor.reconstruction_cutoff = 'contract_end'
    ctx.editor.monthly_fees = []
    await ctx.saveTerms()
    expect(api.put).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('自定义计费明细'))
  })
})

describe('ResultPanel：勾稽检查、摘要与记录分页', () => {
  it('runCheck 调 POST check 并刷新摘要与批次', async () => {
    api.post.mockResolvedValueOnce({ data: { batch_id: 7, status: 'ready', checks: [] } })
    api.get.mockResolvedValueOnce({ data: { batch: BATCH, checks: [], totals: {}, unknown_reasons: [] } })
    let refreshed = false
    const emit = (name) => {
      if (name === 'refresh') refreshed = true
    }
    const ctx = ResultPanel.setup({ batch: BATCH }, { expose: () => {}, emit })
    await ctx.runCheck()
    expect(api.post).toHaveBeenCalledWith('/finance/recon/batches/7/check', null, expect.objectContaining({ timeout: expect.any(Number) }))
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/summary')
    expect(refreshed).toBe(true)
  })

  it('check 前置校验 400（缺源/解析失败）不重复弹错且批次状态保持展示', async () => {
    api.post.mockRejectedValueOnce({ response: { status: 400, data: { detail: '缺少源文件：账单' } } })
    const ctx = mountResult({ batch: BATCH })
    await ctx.runCheck()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.checking.value).toBe(false)
  })

  it('摘要勾稽结论区分通过/不一致/待定，unknown 有可读原因', () => {
    const ctx = mountResult({ batch: BATCH })
    ctx.summary.value = {
      batch: BATCH,
      checks: [
        { key: 'bill_vs_arrear', label: '账单=欠费', scope: 'HT-SYN-001', state: 'pass', message: '一致' },
        { key: 'reconstruction', label: '合同应收重构', scope: 'HT-SYN-001', state: 'unknown', message: '未录入口径参数，无法重构合同应收' },
        { key: 'aging', label: '账龄分桶', scope: '总计', state: 'diff', message: '账龄合计不完整' },
      ],
      totals: { bill_total: '1234.56' },
      unknown_reasons: [{ check: 'reconstruction', label: '合同应收重构', scope: 'HT-SYN-001', reason: '未录入口径参数' }],
    }
    expect(ctx.checkRows.value).toHaveLength(3)
    expect(ctx.checkRows.value[0].meta.type).toBe('success')
    expect(ctx.checkRows.value[1].meta.type).toBe('warning')
    expect(ctx.checkRows.value[2].meta.type).toBe('danger')
    expect(ctx.unknownReasons.value).toHaveLength(1)
    expect(ctx.unknownReasons.value[0].reason).toContain('未录入口径参数')
  })

  it('records 分页过滤：空过滤不发送 kind/contract/doc 参数，页码翻页', async () => {
    api.get.mockResolvedValue({ data: { total: 0, page: 2, page_size: 20, items: [] } })
    const ctx = mountResult({ batch: BATCH })
    ctx.recordPage.value = 2
    await ctx.loadRecords()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/records', { params: { page: 2, page_size: 20 } })
    ctx.recordQuery.kind = 'claim'
    ctx.recordQuery.contract = 'HT-SYN-001'
    ctx.recordQuery.doc = ''
    await ctx.loadRecords()
    expect(api.get).toHaveBeenLastCalledWith('/finance/recon/batches/7/records', {
      params: { page: 2, page_size: 20, kind: 'claim', contract: 'HT-SYN-001' },
    })
    expect(ctx.records.value).toEqual([]) // 空态
  })

  it('ready + needs_recheck 可重跑：canCheck=true 且 runCheck 发 POST（与后端 TRANSITIONS ready→checking 对齐）', async () => {
    const readyBatch = { ...BATCH, status: 'ready', status_label: '已就绪', needs_recheck: true, ever_ready: true }
    api.post.mockResolvedValueOnce({ data: { batch_id: 7, status: 'ready', status_label: '已就绪', checks: [] } })
    api.get.mockResolvedValueOnce({ data: { batch: readyBatch, checks: [], totals: {}, unknown_reasons: [] } })
    const ctx = mountResult({ batch: readyBatch })
    expect(ctx.canCheck.value).toBe(true)
    expect(ctx.checkBlockReason.value).toBe('')
    await ctx.runCheck()
    expect(api.post).toHaveBeenCalledWith('/finance/recon/batches/7/check', null, expect.objectContaining({ timeout: expect.any(Number) }))
  })

  it('ever_ready + failed 可重试：canCheck=true（ever_ready 只拦截资料替换，不拦截重跑）', () => {
    const ctx = mountResult({ batch: { ...BATCH, status: 'failed', status_label: '失败', ever_ready: true, error_summary: '上一轮引擎失败' } })
    expect(ctx.canCheck.value).toBe(true)
  })

  it('checking 状态禁用检查并给出中文原因', () => {
    const ctx = mountResult({ batch: { ...BATCH, status: 'checking', status_label: '检查中' } })
    expect(ctx.canCheck.value).toBe(false)
    expect(ctx.checkBlockReason.value).toContain('检查中')
  })
})

describe('ReviewPanel：匹配建议、人工分摊与问题复核', () => {
  // 每个用例新造 fixture：组件会就地更新行状态，共享对象会把状态泄漏进后续用例
  const makeMatch = (overrides = {}) => ({
    id: 11, batch_id: 7, status: 'suggested', status_label: '待确认', origin: 'algorithm',
    allocated_amount: '500.00', note: '机检建议（精确单据引用）',
    claim: { record_id: 31, kind: 'claim', doc_no: 'SK-SYN-0001', contract_no: '', fee_type: '', occur_date: '2026-11-06', amount_1: '500.00', amount_2: '500.00' },
    writeoff: { record_id: 1, kind: 'writeoff', doc_no: 'DZ-SYN-0001', contract_no: 'HT-SYN-001', fee_type: '固定租金', occur_date: null, amount_1: '100.00', amount_2: '100.00' },
    updated_at: '',
    ...overrides,
  })
  const makeIssue = (overrides = {}) => ({
    id: 21, batch_id: 7, origin: 'machine', fingerprint: 'f1', category: 'bill_arrear_mismatch',
    category_label: '账单与欠费不一致', severity: 'warn', evidence: ['证据一'], amount_impact: '1.00',
    status: 'pending', status_label: '待确认', active: true, note: '', last_seen: '', created_at: '', updated_at: '',
    ...overrides,
  })
  const MATCH_ROW = makeMatch()
  const ISSUE_ROW = makeIssue()

  it('loadMatches 拉取匹配清单并初始化分摊草稿', async () => {
    api.get.mockResolvedValueOnce({ data: { total: 1, items: [makeMatch()] } })
    const ctx = mountReview({ batch: BATCH })
    await ctx.loadMatches()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/matches')
    expect(ctx.matches.value).toHaveLength(1)
    expect(ctx.allocDrafts[11]).toBe(500)
  })

  it('部分分摊后确认：PATCH 携带调整后的分摊金额', async () => {
    api.get.mockResolvedValueOnce({ data: { total: 1, items: [makeMatch()] } })
    api.patch.mockResolvedValueOnce({ data: makeMatch({ status: 'confirmed', status_label: '已确认' }) })
    api.get.mockResolvedValue({ data: { total: 1, items: [makeMatch({ status: 'confirmed', status_label: '已确认' })] } })
    const ctx = mountReview({ batch: BATCH })
    await ctx.loadMatches()
    ctx.setAllocation(makeMatch(), 300)
    await ctx.confirmMatch(makeMatch())
    expect(api.patch).toHaveBeenCalledWith('/finance/recon/matches/11', { status: 'confirmed', allocated_amount: '300.00' })
    expect(ctx.matches.value[0].status).toBe('confirmed')
  })

  it('拒绝建议：PATCH status=rejected，不携带分摊金额', async () => {
    api.patch.mockResolvedValueOnce({ data: makeMatch({ status: 'rejected', status_label: '已排除' }) })
    const ctx = mountReview({ batch: BATCH })
    await ctx.rejectMatch(makeMatch())
    expect(api.patch).toHaveBeenCalledWith('/finance/recon/matches/11', { status: 'rejected' })
  })

  it('确认超额 409：统一提示、不重复弹错、清单保持原样', async () => {
    api.patch.mockRejectedValueOnce({ response: { status: 409, data: { detail: '分摊超额：认领记录 #31 累计分摊 800.00 已超过认领金额 500.00' } } })
    api.get.mockResolvedValueOnce({ data: { total: 1, items: [makeMatch()] } })
    const ctx = mountReview({ batch: BATCH })
    await ctx.loadMatches()
    await ctx.confirmMatch(makeMatch())
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.matches.value[0].status).toBe('suggested')
  })

  it('人工新增匹配：POST 三元组 + 分摊金额', async () => {
    api.get.mockImplementation((url) => {
      if (url === '/finance/recon/batches/7/records') {
        return Promise.resolve({ data: { total: 1, items: [{ id: 31, kind: 'claim', doc_no: 'SK-SYN-0001' }] } })
      }
      return Promise.resolve({ data: {} })
    })
    api.post.mockResolvedValueOnce({ data: { id: 12 } })
    const ctx = mountReview({ batch: BATCH })
    await ctx.loadMatchOptions()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/records', { params: { page: 1, page_size: 200, kind: 'claim' } })
    ctx.matchForm.claim_record_id = 31
    ctx.matchForm.writeoff_record_id = 1
    ctx.matchForm.allocated_amount = 120
    await ctx.createMatch()
    expect(api.post).toHaveBeenCalledWith('/finance/recon/batches/7/matches', {
      claim_record_id: 31,
      writeoff_record_id: 1,
      allocated_amount: '120.00',
      note: '',
    })
  })

  it('问题清单：pendingCount 只数待确认且 active 的问题', async () => {
    api.get.mockResolvedValueOnce({ data: { total: 2, items: [ISSUE_ROW, { ...ISSUE_ROW, id: 22, status: 'dismissed', active: false }] } })
    const ctx = mountReview({ batch: BATCH })
    await ctx.loadIssues()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/issues', { params: {} })
    expect(ctx.pendingCount.value).toBe(1)
  })

  it('确认/排除问题必须填写说明，空说明不发请求；带说明走 PATCH（弹窗唯一入口）', async () => {
    api.patch.mockResolvedValue({ data: ISSUE_ROW })
    const ctx = mountReview({ batch: BATCH })
    ctx.issues.value = [ISSUE_ROW]
    ctx.askResolve(ISSUE_ROW, 'confirmed')
    ctx.resolveNote.value = '   '
    await ctx.submitResolve()
    expect(api.patch).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
    ctx.resolveNote.value = '人工核对账单口径一致'
    await ctx.submitResolve()
    expect(api.patch).toHaveBeenCalledWith('/finance/recon/issues/21', { status: 'confirmed', note: '人工核对账单口径一致' })
    expect(ctx.resolveDialog.value).toBe(false)
    ctx.askResolve(ISSUE_ROW, 'dismissed')
    ctx.resolveNote.value = '重复项，已合并'
    await ctx.submitResolve()
    expect(api.patch).toHaveBeenLastCalledWith('/finance/recon/issues/21', { status: 'dismissed', note: '重复项，已合并' })
  })

  it('人工新增问题：POST category/severity/note', async () => {
    api.post.mockResolvedValueOnce({ data: { id: 23 } })
    const ctx = mountReview({ batch: BATCH })
    ctx.issueForm.category = '保证金重复核销'
    ctx.issueForm.severity = 'error'
    ctx.issueForm.note = '需要人工核对手工台账'
    await ctx.createIssue()
    expect(api.post).toHaveBeenCalledWith('/finance/recon/batches/7/issues', {
      category: '保证金重复核销',
      severity: 'error',
      note: '需要人工核对手工台账',
      amount_impact: null,
      evidence: [],
    })
    expect(ctx.issueForm.category).toBe('')
  })

  it('三格式导出：blob 请求 + saveBlob 显式文件名', async () => {
    const blob = new Blob(['xlsx'])
    api.get.mockResolvedValue({ data: blob })
    const ctx = mountReview({ batch: BATCH })
    await ctx.download('xlsx')
    await ctx.download('md')
    await ctx.download('docx')
    const urls = api.get.mock.calls.map((c) => c[0])
    expect(urls).toEqual([
      '/finance/recon/batches/7/export/xlsx',
      '/finance/recon/batches/7/export/report.md',
      '/finance/recon/batches/7/export/report.docx',
    ])
    expect(api.get.mock.calls[0][1]).toMatchObject({ responseType: 'blob' })
    expect(saveBlob).toHaveBeenCalledTimes(3)
    expect(saveBlob).toHaveBeenNthCalledWith(1, blob, '11月商户核销对账_商户核销对账_2026-11-18.xlsx')
  })

  it('needs_recheck 未重跑：导出前警示（不擅自阻断，确认后仍尝试）', async () => {
    api.get.mockResolvedValue({ data: new Blob(['md']) })
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    const ctx = mountReview({ batch: { ...BATCH, needs_recheck: true, status: 'ready' } })
    await ctx.download('md')
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('重新执行勾稽检查'),
      expect.anything(),
      expect.anything(),
    )
    expect(api.get).toHaveBeenCalled()
    expect(saveBlob).toHaveBeenCalled()
  })

  it('存在未决问题：导出前明确提示未决事项数量，确认后仍导出', async () => {
    api.get.mockImplementation((url) =>
      url.endsWith('/issues')
        ? Promise.resolve({ data: { total: 2, items: [ISSUE_ROW, ISSUE_ROW] } })
        : Promise.resolve({ data: new Blob(['xlsx']) }),
    )
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    const ctx = mountReview({ batch: BATCH })
    await ctx.loadIssues()
    await ctx.download('xlsx')
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('2'),
      expect.anything(),
      expect.anything(),
    )
    expect(saveBlob).toHaveBeenCalled()
  })
})

describe('面板首次挂载自动加载（onMounted 即发请求，与父组件调用不重复）', () => {
  it('SourcePanel 挂载拉取源列表与口径参数', async () => {
    api.get.mockImplementation((url) =>
      url.endsWith('/sources')
        ? Promise.resolve({ data: SOURCES })
        : Promise.resolve({ data: { batch_id: 7, items: [], out_of_scope_items: [] } }),
    )
    const ctx = mountSource({ batch: BATCH })
    expect(mountedCbs).toHaveLength(1)
    await fireMounted()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/sources')
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/terms')
    expect(ctx.sources.value).toHaveLength(2)
    expect(ctx.terms.value).toEqual([])
  })

  it('ResultPanel 挂载拉取勾稽摘要与事实记录', async () => {
    api.get.mockImplementation((url) =>
      url.endsWith('/summary')
        ? Promise.resolve({ data: { batch: BATCH, checks: [], totals: {}, unknown_reasons: [] } })
        : Promise.resolve({ data: { total: 0, page: 1, page_size: 20, items: [] } }),
    )
    const ctx = mountResult({ batch: BATCH })
    expect(mountedCbs).toHaveLength(1)
    await fireMounted()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/summary')
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/records', { params: { page: 1, page_size: 20 } })
    expect(ctx.summary.value).not.toBeNull()
    expect(ctx.recordsTotal.value).toBe(0)
  })

  it('ReviewPanel 挂载拉取匹配建议与问题清单', async () => {
    api.get.mockImplementation((url) => Promise.resolve({ data: { total: 0, items: [] } }))
    const ctx = mountReview({ batch: BATCH })
    expect(mountedCbs).toHaveLength(1)
    await fireMounted()
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/matches')
    expect(api.get).toHaveBeenCalledWith('/finance/recon/batches/7/issues', { params: {} })
    expect(ctx.matches.value).toEqual([])
    expect(ctx.issues.value).toEqual([])
  })

  it('未选择批次时挂载不发起任何工作区请求（空态守卫）', async () => {
    const src = mountSource({ batch: null })
    const res = mountResult({ batch: null })
    const rev = mountReview({ batch: null })
    expect(mountedCbs).toHaveLength(3)
    await fireMounted()
    expect(api.get).not.toHaveBeenCalled()
    expect(src.sources.value).toEqual([])
    expect(res.summary.value).toBeNull()
    expect(rev.matches.value).toEqual([])
  })
})
