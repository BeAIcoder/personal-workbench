// Task 6C：POS 前端下载与刷新一致性。
// - 打包下载必须走统一 saveBlob（axios blob + 显式文件名），不再 window.open 裸开 URL；
// - 单文件/合并下载同样走 utils/download.js 的唯一实现；
// - 刷新凭证批次列表时清掉旧的勾选，防止对已不存在的文件发起合并。
// node 环境无 DOM：mock 生命周期钩子、消息组件、api 与下载实现，直接驱动编译后的 setup()。
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
}))

const PosRunView = (await import('../src/modules/finance-pos/views/PosRunView.vue')).default

function mount() {
  return PosRunView.setup({}, { expose: () => {}, emit: () => {} })
}

const runRow = { id: 7, artifacts: ['a.xlsx'], status: 'ok' }

beforeEach(() => {
  vi.resetAllMocks()
  showPdfInWindow.mockReturnValue('blob:pdf-1')
})

describe('POS 打包下载', () => {
  it('走 axios blob + saveBlob，不再 window.open 裸链', async () => {
    const open = vi.fn()
    vi.stubGlobal('open', open)
    const blob = new Blob(['zip'])
    api.get.mockResolvedValue({ data: blob })

    const ctx = mount()
    await ctx.downloadZip(runRow)

    expect(api.get).toHaveBeenCalledTimes(1)
    const [url, options] = api.get.mock.calls[0]
    expect(url).toBe('/finance/pos/runs/7/download')
    expect(options).toMatchObject({ responseType: 'blob' })
    expect(options.timeout).toBeGreaterThanOrEqual(60000)
    expect(saveBlob).toHaveBeenCalledWith(blob, 'POS对账_运行7.zip')
    expect(open).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('下载失败不提示成功、不落文件（拦截器统一提示）', async () => {
    const { ElMessage } = await import('element-plus')
    api.get.mockRejectedValueOnce(new Error('boom'))

    const ctx = mount()
    await ctx.downloadZip(runRow)

    expect(saveBlob).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('单文件下载复用统一 saveBlob 并显式落文件名', async () => {
    const blob = new Blob(['pdf'])
    api.get.mockResolvedValueOnce({ data: blob })

    const ctx = mount()
    await ctx.downloadFile({ id: 3 }, { name: '凭证 001.pdf', size: 1024 })

    expect(api.get).toHaveBeenCalledWith(
      `/finance/pos/vouchers/3/files/${encodeURIComponent('凭证 001.pdf')}`,
      expect.objectContaining({ responseType: 'blob' }),
    )
    expect(saveBlob).toHaveBeenCalledWith(blob, '凭证 001.pdf')
  })
})

describe('POS 刷新清理旧的文件选择', () => {
  it('重新拉取批次后，旧勾选不会残留到同名批次上', async () => {
    api.get.mockResolvedValueOnce({
      data: { items: [{ id: 3, month: '2026-10', files: [{ name: 'old.pdf', size: 10 }], summary: {} }] },
    })
    const ctx = mount()
    await ctx.loadVouchers()
    ctx.fileSelection[3] = ['old.pdf']
    expect(ctx.selectedFiles({ id: 3 })).toEqual(['old.pdf'])

    api.get.mockResolvedValueOnce({
      data: { items: [{ id: 3, month: '2026-10', files: [{ name: 'new.pdf', size: 20 }], summary: {} }] },
    })
    await ctx.loadVouchers()

    expect(ctx.selectedFiles({ id: 3 })).toEqual([])
    expect(ctx.voucherBatches.value[0]._files.map((f) => f.name)).toEqual(['new.pdf'])
  })
})

describe('POS 凭证打印', () => {
  const batch = { id: 3, month: '2026-10' }
  const file = { name: '凭证 001.pdf', size: 1024 }

  it('单份打印先预开窗口，再取文件并交给 PDF 打印工具', async () => {
    const { ElMessage } = await import('element-plus')
    const win = { close: vi.fn() }
    const blob = new Blob(['pdf'])
    let requestsAtOpen = -1
    openPrintWindow.mockImplementation(() => {
      requestsAtOpen = api.get.mock.calls.length
      return win
    })
    api.get.mockResolvedValueOnce({ data: blob })

    const ctx = mount()
    const op = ctx.printVoucherFile(batch, file)
    expect(openPrintWindow).toHaveBeenCalledTimes(1)
    expect(requestsAtOpen).toBe(0)
    expect(ctx.filePrinting['3:凭证 001.pdf']).toBe(true)
    await op

    expect(api.get).toHaveBeenCalledWith(
      `/finance/pos/vouchers/3/files/${encodeURIComponent(file.name)}`,
      { responseType: 'blob', timeout: 300000 },
    )
    expect(showPdfInWindow).toHaveBeenCalledWith(blob, { window: win })
    expect(ElMessage.info).toHaveBeenCalled()
    expect(win.close).not.toHaveBeenCalled()
    expect(ctx.filePrinting['3:凭证 001.pdf']).toBeUndefined()
  })

  it('合并打印按当前勾选顺序调用既有 merge 接口', async () => {
    const win = { close: vi.fn() }
    const blob = new Blob(['merged'])
    openPrintWindow.mockReturnValue(win)
    api.post.mockResolvedValueOnce({ data: blob })
    const ctx = mount()
    ctx.fileSelection[3] = ['a.pdf', 'b.pdf']

    const op = ctx.mergePrint(batch)
    expect(ctx.mergePrinting[3]).toBe(true)
    await op

    expect(api.post).toHaveBeenCalledWith(
      '/finance/pos/vouchers/3/merge',
      { names: ['a.pdf', 'b.pdf'] },
      { responseType: 'blob', timeout: 300000 },
    )
    expect(showPdfInWindow).toHaveBeenCalledWith(blob, { window: win })
    expect(ctx.mergePrinting[3]).toBeUndefined()
  })

  it('未选择文件时不打开窗口也不请求合并', async () => {
    const ctx = mount()
    await ctx.mergePrint(batch)
    expect(openPrintWindow).not.toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('弹窗被拦截时不发文件请求并提示下载兜底', async () => {
    const { ElMessage } = await import('element-plus')
    openPrintWindow.mockReturnValue(null)
    const ctx = mount()
    await ctx.printVoucherFile(batch, file)
    expect(api.get).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('打印请求失败关闭空白页并释放状态', async () => {
    const { ElMessage } = await import('element-plus')
    const win = { close: vi.fn() }
    openPrintWindow.mockReturnValue(win)
    api.get.mockRejectedValueOnce(new Error('missing'))
    const ctx = mount()
    await ctx.printVoucherFile(batch, file)
    expect(win.close).toHaveBeenCalledTimes(1)
    expect(showPdfInWindow).not.toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.filePrinting['3:凭证 001.pdf']).toBeUndefined()
  })

  it('合并 PDF 无法交给新窗口时关闭空白页', async () => {
    const { ElMessage } = await import('element-plus')
    const win = { close: vi.fn() }
    openPrintWindow.mockReturnValue(win)
    showPdfInWindow.mockReturnValue('')
    api.post.mockResolvedValueOnce({ data: new Blob(['merged']) })
    const ctx = mount()
    ctx.fileSelection[3] = ['a.pdf']
    await ctx.mergePrint(batch)
    expect(win.close).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.mergePrinting[3]).toBeUndefined()
  })
})
