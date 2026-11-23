// Task 7 / FE L-123、L-124、L-133 + L-092 前端子项：
// 银行流水预览必须用「序号 + 文件快照」丢弃旧响应；日期为空不得抛 TypeError；
// 预览/模板生成按长任务放宽超时；下载走 saveBlob（挂 DOM + 延迟 revoke）。
// T6D：导出改为按 account_token 选择/反查（掩码仅展示），同掩码账号可分别导出。
import { readFileSync } from 'node:fs'
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

const source = readFileSync(
  new URL('../src/modules/finance-bankflow/views/BankflowView.vue', import.meta.url),
  'utf8',
)

function mount() {
  return BankflowView.setup({}, { expose: () => {}, emit: () => {} })
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

// 后端预览只回掩码 + 稳定令牌；前端禁止再持有完整账号
function acc(over = {}) {
  return {
    account: '************0354',
    account_token: 'tok-default',
    tail: '0354',
    name: '示例项目',
    ent: 'H003002',
    rows: 2,
    date_min: '20261001',
    date_max: '20261031',
    bank_label: '建行',
    ...over,
  }
}

function previewBody(...rows) {
  const accounts = rows.length ? rows : [acc()]
  return { data: { total: 2, accounts } }
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

    second.resolve(previewBody(acc({ account: '**********0956', account_token: 'tok-b', tail: '0956' })))
    await pendingB
    first.resolve(previewBody(acc({ account: '**********0354', account_token: 'tok-a', tail: '0354' })))
    await pendingA

    expect(ctx.summary.value.accounts[0].tail).toBe('0956')
    expect(ctx.selectedToken.value).toBe('tok-b')
    expect(ctx.selectedAccount.value).toBe('**********0956')
    // 预览用的必须是 B 的快照，下载不会拿旧文件 A
    expect(ctx.previewFile.value).toBe(fileB)
  })

  it('日期为 null 时不因 .slice 抛错，未选到日期区间也不崩', () => {
    const ctx = mount()
    expect(() => ctx.onSelectAccount(acc({ date_min: null, date_max: null }))).not.toThrow()
    expect(ctx.dateRange.value).toEqual([])
  })

  it('预览与模板生成按长任务放宽到 120s/180s', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile('a.xlsx')
    api.post.mockResolvedValueOnce(previewBody())
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
    api.post.mockResolvedValueOnce(previewBody())
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
    api.post.mockResolvedValueOnce(previewBody())
    await ctx.parseFile()
    api.post.mockRejectedValueOnce(new Error('账号未映射'))
    await ctx.downloadTemplate()
    expect(saveBlob).not.toHaveBeenCalled()
  })

  it('预览后文件被替换：拒绝用旧快照生成模板', async () => {
    const ctx = mount()
    const fileA = fakeFile('a.xlsx')
    ctx.file.value = fileA
    api.post.mockResolvedValueOnce(previewBody())
    await ctx.parseFile()
    ctx.file.value = fakeFile('b.xlsx')
    await ctx.downloadTemplate()
    expect(saveBlob).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledTimes(1)
  })
})

describe('Bankflow 账号令牌通道（T6D）', () => {
  it('选择键是 account_token；掩码仅用于展示', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile('a.xlsx')
    api.post.mockResolvedValueOnce(previewBody())
    await ctx.parseFile()
    expect(ctx.selectedToken.value).toBe('tok-default')
    expect(ctx.selectedAccount.value).toBe('************0354')
  })

  it('同掩码同尾号两账号可分别选择并各自按令牌导出', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile('a.xlsx')
    const first = acc({ account_token: 'tok-first', ent: 'H1', name: '甲公司' })
    const second = acc({ account_token: 'tok-second', ent: 'H2', name: '乙公司' })
    api.post.mockResolvedValueOnce(previewBody(first, second))
    await ctx.parseFile()
    // 默认选中第一个
    expect(ctx.selectedToken.value).toBe('tok-first')

    ctx.selectAccount(second)
    expect(ctx.selectedToken.value).toBe('tok-second')

    api.post.mockResolvedValueOnce({ data: new Blob(['zip']) })
    await ctx.downloadTemplate()
    const body = api.post.mock.calls[1][1]
    expect(body.get('account_token')).toBe('tok-second')
    // 不再回传掩码 account（后端掩码歧义会给 400）
    expect(body.get('account')).toBeNull()
  })

  it('文件名用行内 tail，不按掩码截取', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile('a.xlsx')
    api.post.mockResolvedValueOnce(previewBody(acc({ account: '****************0001', account_token: 'tok-x', tail: '0001' })))
    await ctx.parseFile()
    api.post.mockResolvedValueOnce({ data: new Blob(['zip']) })
    await ctx.downloadTemplate()
    expect(saveBlob.mock.calls[0][1]).toBe('建行0001_银行流水导入模板_20261001-20261031.xlsx')
  })

  it('selectedMapped 按令牌定位行：未映射账号禁用导出', async () => {
    const ctx = mount()
    ctx.file.value = fakeFile('a.xlsx')
    const unmapped = acc({ account_token: 'tok-n', ent: '' })
    api.post.mockResolvedValueOnce(previewBody(unmapped))
    await ctx.parseFile()
    expect(ctx.selectedToken.value).toBe('tok-n')
    expect(ctx.selectedMapped.value).toBe(false)
    expect(source).toContain(':disabled="!selectedToken || !selectedMapped"')
  })
})

describe('Bankflow 视图契约（静态）', () => {
  it('radio 以 account_token 为值，导出只传 account_token', () => {
    expect(source).toContain(':value="row.account_token"')
    expect(source).toContain("fd.append('account_token'")
    expect(source).not.toContain("fd.append('account'")
    expect(source).toContain('acc.tail')
    expect(source).not.toContain('selectedAccount.value.slice(-4)')
  })
})
