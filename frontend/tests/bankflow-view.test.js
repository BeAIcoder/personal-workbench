// Task 7 / FE L-123、L-124、L-133 + L-092 前端子项：
// 银行流水预览必须用「序号 + 文件快照」丢弃旧响应；日期为空不得抛 TypeError；
// 预览/模板生成按长任务放宽超时；下载走 saveBlob（挂 DOM + 延迟 revoke）。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  defaults: { baseURL: '/api' },
}))
const saveBlob = vi.hoisted(() => vi.fn(() => true))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }), onMounted: () => {} }
})
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('../src/api', () => ({ default: api, BACKEND_HINT: '测试后端提示' }))
vi.mock('../src/utils/download.js', () => ({ saveBlob, REVOKE_DELAY_MS: 10000 }))

const { compactDateToRange, isPreviewCurrent } = await import('../src/modules/finance-bankflow/previewState.js')
const BankflowView = (await import('../src/modules/finance-bankflow/views/BankflowView.vue')).default

function mount() {
  return BankflowView.setup({}, { expose: () => {}, emit: () => {} })
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function previewBody(account, dates = ['20261001', '20261031']) {
  return {
    data: {
      total: 2,
      accounts: [{
        account,
        tail: account.slice(-4),
        name: '示例项目',
        ent: 'H003002',
        rows: 2,
        date_min: dates[0],
        date_max: dates[1],
        bank_label: '建行',
      }],
    },
  }
}

function fakeFile(name) {
  return { name }
}

beforeEach(() => {
  vi.clearAllMocks()
  saveBlob.mockReturnValue(true)
})

describe('银行流水日期与竞态纯逻辑', () => {
  it('8 位日期转区间；null/空/非法返回空数组而不是抛错', () => {
    expect(compactDateToRange('20261001', '20261031')).toEqual(['2026-10-01', '2026-10-31'])
    expect(compactDateToRange(null, undefined)).toEqual([])
    expect(compactDateToRange('', '')).toEqual([])
    expect(compactDateToRange('2026-10-01', '20261031')).toEqual([])
  })

  it('旧序号/旧文件快照不算当前预览', () => {
    const file = fakeFile('a.xlsx')
    expect(isPreviewCurrent(2, 2, file, file)).toBe(true)
    expect(isPreviewCurrent(1, 2, file, file)).toBe(false)
    expect(isPreviewCurrent(2, 2, file, fakeFile('b.xlsx'))).toBe(false)
  })
})

describe('Bankflow 预览竞态与超时', () => {
  it('慢解析期间换文件：旧响应到达后不得覆盖当前预览', async () => {
    const ctx = mount()
    const fileA = fakeFile('a.xlsx')
    const fileB = fakeFile('b.xlsx')
    const first = deferred()
    const second = deferred()
    api.post
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)

    ctx.file.value = fileA
    const pendingA = ctx.parseFile()
    ctx.onFileChange({ raw: fileB })
    const pendingB = ctx.parseFile()

    second.resolve(previewBody('6222000000000956'))
    await pendingB
    first.resolve(previewBody('6227000000000354'))
    await pendingA

    expect(ctx.summary.value.accounts[0].account).toBe('6222000000000956')
    expect(ctx.selectedAccount.value).toBe('6222000000000956')
    // 预览用的必须是 B 的快照，下载不会拿旧文件 A
    expect(ctx.previewFile.value).toBe(fileB)
  })

  it('日期为 null 时不因 .slice 抛错，未选到日期区间也不崩', () => {
    const ctx = mount()
    expect(() => ctx.onSelectAccount({ account: '1', date_min: null, date_max: null })).not.toThrow()
    expect(ctx.dateRange.value).toEqual([])
  })

  it('预览与模板生成按长任务放宽到 120s/180s', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile('a.xlsx')
    api.post.mockResolvedValueOnce(previewBody('6227000000000354'))
    await ctx.parseFile()
    expect(api.post.mock.calls[0][2]).toMatchObject({ timeout: 120000 })

    api.post.mockResolvedValueOnce({ data: new Blob(['zip']) })
    await ctx.downloadTemplate()
    expect(api.post.mock.calls[1][2]).toMatchObject({ responseType: 'blob', timeout: 180000 })
  })
})

describe('Bankflow 下载行为', () => {
  it('生成成功用 saveBlob 显式落名（不立即 revoke）', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile('a.xlsx')
    api.post.mockResolvedValueOnce(previewBody('6227000000000354'))
    await ctx.parseFile()
    api.post.mockResolvedValueOnce({ data: new Blob(['zip']) })
    await ctx.downloadTemplate()
    expect(saveBlob).toHaveBeenCalledTimes(1)
    const [blob, name] = saveBlob.mock.calls[0]
    expect(blob).toBeInstanceOf(Blob)
    expect(name).toBe('建行0354_银行流水导入模板_20261001-20261031.xlsx')
  })

  it('下载失败不调用 saveBlob，也不误报后端未启动（提示由拦截器给 detail）', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile('a.xlsx')
    api.post.mockResolvedValueOnce(previewBody('6227000000000354'))
    await ctx.parseFile()
    api.post.mockRejectedValueOnce(new Error('账号未映射'))
    await ctx.downloadTemplate()
    expect(saveBlob).not.toHaveBeenCalled()
  })

  it('预览后文件被替换：拒绝用旧快照生成模板', async () => {
    const ctx = mount()
    const fileA = fakeFile('a.xlsx')
    ctx.file.value = fileA
    api.post.mockResolvedValueOnce(previewBody('6227000000000354'))
    await ctx.parseFile()
    ctx.file.value = fakeFile('b.xlsx')
    await ctx.downloadTemplate()
    expect(saveBlob).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledTimes(1)
  })
})
