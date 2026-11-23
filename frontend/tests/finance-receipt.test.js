// 银行回单整理前端：账期建议、扫描与复核、待确认阻断、下载/打印/合并/ZIP 统一走 saveBlob 与打印工具。
// node 环境无 DOM：mock 生命周期钩子、消息组件、api 与下载实现，直接驱动编译后的 setup()。
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
const openPrintWindow = vi.hoisted(() => vi.fn())
const showPdfInWindow = vi.hoisted(() => vi.fn())

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
    onUnmounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { prompt: vi.fn(), confirm: vi.fn() },
}))

vi.mock('../src/api', () => ({ default: api, API_BASE: '/api' }))
vi.mock('../src/utils/download.js', () => ({
  saveBlob,
  openPrintWindow,
  showPdfInWindow,
  REVOKE_DELAY_MS: 10000,
  PRINT_REVOKE_DELAY_MS: 600000,
}))

const receiptModule = await import('../src/modules/finance-receipt/manifest.js')
const ReceiptView = (await import('../src/modules/finance-receipt/views/ReceiptView.vue')).default

function mount() {
  return ReceiptView.setup({}, { expose: () => {}, emit: () => {} })
}

function batchFixture(overrides = {}) {
  return {
    id: 3,
    period_label: '2026-10',
    start_date: '2026-09-25',
    end_date: '2026-10-31',
    status: 'scanned',
    failed_sources: [],
    warnings: [],
    files: [],
    summary: {
      total: 3,
      counts: {
        pending_clearing_income: 1,
        transfer_income: 1,
        transfer_expense: 1,
        bank_interest_income: 0,
        bank_fee_expense: 0,
        unclassified: 0,
      },
      excluded: 0,
      pending_confirm: 0,
      duplicates: 0,
    },
    items: [
      { id: 11, category: 'transfer_income', included: true, reviewed: false, bank: '建行', account_no: '1234', rdate: '2026-10-05', amount: '100.00', payer: '甲', payee: '', purpose: '', page: 1 },
      { id: 12, category: 'pending_clearing_income', included: true, reviewed: false, bank: '建行', account_no: '1234', rdate: '2026-10-06', amount: '900.00', payer: '待清算', payee: '', purpose: '', page: 1 },
      { id: 13, category: 'transfer_expense', included: true, reviewed: false, bank: '中行', account_no: '5971', rdate: '2026-10-07', amount: '50.00', payer: '', payee: '乙', purpose: '', page: 1 },
    ],
    ...overrides,
  }
}

beforeAll(() => {
  if (!URL.createObjectURL) URL.createObjectURL = () => 'blob:stub'
  if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {}
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:stub-url')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
})

beforeEach(() => {
  vi.resetAllMocks()
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:stub-url')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  showPdfInWindow.mockReturnValue('blob:pdf-1')
})

describe('银行回单整理模块注册', () => {
  it('挂在财务账务处理子分组下，路由为 /finance/receipt', () => {
    expect(receiptModule.default.routes[0].path).toBe('/finance/receipt')
    expect(receiptModule.default.menu).toMatchObject({
      group: '财务',
      subgroup: '账务处理',
      title: '银行回单整理',
      path: '/finance/receipt',
    })
  })
})

describe('账期与扫描', () => {
  it('选择月份后给出可编辑建议：上月 25 日至当月末，手动修改不被覆盖', () => {
    const ctx = mount()
    ctx.applySuggestedPeriod('2026-10')
    expect(ctx.periodLabel.value).toBe('2026-10')
    expect(ctx.startDate.value).toBe('2026-09-25')
    expect(ctx.endDate.value).toBe('2026-10-31')
    ctx.startDate.value = '2026-10-23'
    expect(ctx.startDate.value).toBe('2026-10-23')
  })

  it('扫描提交账期起止与源/输出目录，并展示返回批次', async () => {
    api.post.mockResolvedValueOnce({ data: batchFixture() })
    api.get.mockResolvedValueOnce({ data: { items: [] } })
    const ctx = mount()
    ctx.sourceDir.value = 'fixtures/receipts'
    ctx.outputDir.value = 'fixtures/receipt-output'
    ctx.periodLabel.value = '2026-10'
    ctx.startDate.value = '2026-10-01'
    ctx.endDate.value = '2026-10-31'
    await ctx.scan()
    const [url, body] = api.post.mock.calls[0]
    expect(url).toBe('/finance/receipt/scan')
    expect(body).toMatchObject({
      source_dir: 'fixtures/receipts',
      period_label: '2026-10',
      start_date: '2026-10-01',
      end_date: '2026-10-31',
      output_dir: 'fixtures/receipt-output',
    })
    expect(ctx.batch.value.id).toBe(3)
    expect(ctx.canGenerate.value).toBe(true)
  })
})

describe('逐笔复核与生成阻断', () => {
  it('修改分类或纳入状态后调用 PATCH 并刷新批次详情', async () => {
    api.patch.mockResolvedValueOnce({ data: {} })
    api.get.mockResolvedValueOnce({ data: batchFixture() })
    const ctx = mount()
    ctx.batch.value = batchFixture()
    await ctx.patchItem({ id: 13 }, { category: 'transfer_income' })
    expect(api.patch).toHaveBeenCalledWith('/finance/receipt/batches/3/items/13', { category: 'transfer_income' })
    expect(api.get).toHaveBeenCalledWith('/finance/receipt/batches/3')
  })

  it('仍有待确认项时不发起生成请求', async () => {
    const { ElMessage } = await import('element-plus')
    const ctx = mount()
    ctx.batch.value = batchFixture({
      summary: { ...batchFixture().summary, pending_confirm: 2, counts: { ...batchFixture().summary.counts, unclassified: 2 } },
    })
    expect(ctx.canGenerate.value).toBe(false)
    await ctx.generate()
    expect(api.post).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('生成成功后替换批次并保留产物列表', async () => {
    api.post.mockResolvedValueOnce({
      data: batchFixture({ status: 'ok', files: [{ id: 'f001', name: 'a.pdf', kind: 'category', count: 1, pages: 1 }] }),
    })
    api.get.mockResolvedValueOnce({ data: { items: [] } })
    const ctx = mount()
    ctx.batch.value = batchFixture()
    await ctx.generate()
    expect(api.post).toHaveBeenCalledWith('/finance/receipt/batches/3/generate', null, expect.any(Object))
    expect(ctx.batch.value.status).toBe('ok')
  })
})

describe('下载、打印与合并', () => {
  const row = { id: 3, period_label: '2026-10', files: [] }

  it('单文件下载走 axios blob 与 saveBlob 显式文件名', async () => {
    const blob = new Blob(['pdf'])
    api.get.mockResolvedValueOnce({ data: blob })
    const ctx = mount()
    await ctx.downloadFile(row, { id: 'f001', name: '建行1234_正常转账收入_2026-10.pdf' })
    const [url, options] = api.get.mock.calls[0]
    expect(url).toBe('/finance/receipt/batches/3/files/f001')
    expect(options).toMatchObject({ responseType: 'blob' })
    expect(saveBlob).toHaveBeenCalledWith(blob, '建行1234_正常转账收入_2026-10.pdf')
  })

  it('单份打印在用户手势内预开页签，再交给 showPdfInWindow', async () => {
    const win = { close: vi.fn() }
    openPrintWindow.mockReturnValueOnce(win)
    const blob = new Blob(['pdf'])
    api.get.mockResolvedValueOnce({ data: blob })
    const ctx = mount()
    await ctx.printFile(row, { id: 'f001', name: 'a.pdf', kind: 'category' })
    expect(openPrintWindow).toHaveBeenCalledTimes(1)
    expect(showPdfInWindow).toHaveBeenCalledWith(blob, { window: win })
    expect(win.close).not.toHaveBeenCalled()
  })

  it('合并下载只提交选中的分册编号', async () => {
    const blob = new Blob(['merged'])
    api.post.mockResolvedValueOnce({ data: blob })
    const ctx = mount()
    ctx.onFileSelect(row, [{ id: 'f001' }, { id: 'f002' }])
    await ctx.mergeDownload(row)
    expect(api.post).toHaveBeenCalledWith('/finance/receipt/batches/3/merge', { file_ids: ['f001', 'f002'] }, { responseType: 'blob' })
    expect(saveBlob).toHaveBeenCalledWith(blob, '银行回单合并_2026-10_2份.pdf')
  })

  it('整批 ZIP 下载走 blob 与 saveBlob', async () => {
    const blob = new Blob(['zip'])
    api.get.mockResolvedValueOnce({ data: blob })
    const ctx = mount()
    await ctx.zipDownload(row)
    expect(api.get.mock.calls[0][0]).toBe('/finance/receipt/batches/3/download')
    expect(saveBlob).toHaveBeenCalledWith(blob, '银行回单整理_2026-10.zip')
  })

  it('原单预览在预开页签中导航到 blob，不自动打印', async () => {
    const win = { location: { href: '' }, close: vi.fn() }
    openPrintWindow.mockReturnValueOnce(win)
    api.get.mockResolvedValueOnce({ data: new Blob(['pdf']) })
    const ctx = mount()
    ctx.batch.value = batchFixture()
    await ctx.previewItem({ id: 11 })
    expect(api.get.mock.calls[0][0]).toBe('/finance/receipt/batches/3/items/11/preview')
    expect(win.location.href).toBe('blob:stub-url')
    expect(showPdfInWindow).not.toHaveBeenCalled()
  })
})
