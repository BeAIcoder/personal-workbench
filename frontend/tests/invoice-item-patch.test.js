// Task 7 / FE L-128：发票明细 blur 编辑必须按字段 PATCH，并用序号丢弃旧响应。
// 旧实现一次提交 project_name+remark 两个字段且无序号：先编辑项目、再编辑备注时，
// 先发的请求晚返回会把备注覆盖回旧值；失败也不回滚。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  defaults: { baseURL: '/api' },
}))
const route = vi.hoisted(() => ({ params: { id: '1' }, query: {} }))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }), onMounted: () => {} }
})
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ push: vi.fn() }) }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('../src/api', () => ({ default: api, BACKEND_HINT: '测试后端提示' }))
vi.mock('../src/utils/download.js', () => ({ saveBlob: vi.fn(() => true), REVOKE_DELAY_MS: 10000 }))

const { createItemPatcher } = await import('../src/modules/finance-invoice/itemPatch.js')
const BatchDetailView = (await import('../src/modules/finance-invoice/views/BatchDetailView.vue')).default

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function batchBody() {
  return {
    data: {
      id: 1,
      status: 'parsed',
      file_count: 1,
      download_name: '发票汇总2026年10月.zip',
      items: [
        { id: 7, invoice_number: '10001', project_name: '原项目', remark: '原备注', extraction_success: true },
        { id: 8, invoice_number: '10002', project_name: '行八项目', remark: '行八备注', extraction_success: true },
      ],
    },
  }
}

function mount() {
  return BatchDetailView.setup({}, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation(async (url) => {
    if (url.includes('/projects')) return { data: [] }
    return batchBody()
  })
})

describe('createItemPatcher 纯行为', () => {
  it('同字段旧响应晚到被丢弃，只有最新一次生效', async () => {
    const applied = []
    const send = vi.fn()
    const first = deferred()
    const second = deferred()
    send.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const patcher = createItemPatcher({ send, onApplied: (id, res) => applied.push(res) })

    const p1 = patcher.patch(7, 'project_name', '第一次')
    const p2 = patcher.patch(7, 'project_name', '第二次')
    second.resolve({ project_name: '第二次' })
    await p2
    first.resolve({ project_name: '第一次' })
    await p1

    expect(applied).toEqual([{ project_name: '第二次' }])
  })

  it('不同字段互不阻塞，旧响应只更新自己的字段', async () => {
    const rows = { project_name: '原项目', remark: '原备注' }
    const send = vi.fn()
    const project = deferred()
    const remark = deferred()
    send.mockReturnValueOnce(project.promise).mockReturnValueOnce(remark.promise)
    const patcher = createItemPatcher({
      send,
      onApplied: (id, res) => Object.assign(rows, res),
    })
    const pProject = patcher.patch(7, 'project_name', '新项目')
    const pRemark = patcher.patch(7, 'remark', '新备注')
    remark.resolve({ remark: '新备注' })
    await pRemark
    project.resolve({ project_name: '新项目', remark: '原备注' })
    await pProject
    expect(rows).toEqual({ project_name: '新项目', remark: '新备注' })
  })

  it('最新一次失败触发 onError（供视图重载恢复），旧请求失败不再打扰', async () => {
    const onError = vi.fn()
    const send = vi.fn()
    const first = deferred()
    const second = deferred()
    send.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const patcher = createItemPatcher({ send, onError })

    const p1 = patcher.patch(7, 'remark', '旧')
    const p2 = patcher.patch(7, 'remark', '新')
    second.reject(new Error('保存失败'))
    await p2
    first.reject(new Error('旧请求失败'))
    await p1
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0][0]).toBe(7)
  })
})

describe('发票详情按字段提交', () => {
  it('编辑项目只发 project_name，不夹带备注旧值', async () => {
    const ctx = mount()
    await ctx.load()
    const row = ctx.batch.value.items[0]
    row.project_name = '新项目'
    api.patch.mockResolvedValue({ data: { id: 7, project_name: '新项目', remark: '原备注' } })
    await ctx.patchItemField(row, 'project_name')
    expect(api.patch).toHaveBeenCalledTimes(1)
    expect(api.patch.mock.calls[0][1]).toEqual({ project_name: '新项目' })
  })

  it('两个字段连续编辑，晚到的项目响应不覆盖新备注', async () => {
    const ctx = mount()
    await ctx.load()
    const row = ctx.batch.value.items[0]
    const project = deferred()
    const remark = deferred()
    api.patch
      .mockImplementationOnce(() => project.promise)
      .mockImplementationOnce(() => remark.promise)

    row.project_name = '新项目'
    const pProject = ctx.patchItemField(row, 'project_name')
    row.remark = '新备注'
    const pRemark = ctx.patchItemField(row, 'remark')

    remark.resolve({ data: { id: 7, remark: '新备注' } })
    await pRemark
    project.resolve({ data: { id: 7, project_name: '新项目', remark: '原备注' } })
    await pProject

    expect(row.project_name).toBe('新项目')
    expect(row.remark).toBe('新备注')
  })

  it('清空项目时发送空串（后端按清空处理），不是 null', async () => {
    const ctx = mount()
    await ctx.load()
    const row = ctx.batch.value.items[0]
    row.project_name = ''
    api.patch.mockResolvedValueOnce({ data: { id: 7, project_name: '', remark: '原备注' } })
    await ctx.patchItemField(row, 'project_name')
    expect(api.patch.mock.calls[0][1]).toEqual({ project_name: '' })
    expect(row.project_name).toBe('')
  })

  it('清空备注时发送空串并落到表格', async () => {
    const ctx = mount()
    await ctx.load()
    const row = ctx.batch.value.items[0]
    row.remark = ''
    api.patch.mockResolvedValueOnce({ data: { id: 7, project_name: '原项目', remark: '' } })
    await ctx.patchItemField(row, 'remark')
    expect(api.patch.mock.calls[0][1]).toEqual({ remark: '' })
    expect(row.remark).toBe('')
  })

  it('两行并发一行失败：只回滚失败行，其它行未提交编辑保留且不整体重载', async () => {
    const ctx = mount()
    await ctx.load()
    const row7 = ctx.batch.value.items[0]
    const row8 = ctx.batch.value.items[1]
    const first = deferred()
    const second = deferred()
    api.patch
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)

    row7.project_name = '行七新项目'
    row8.remark = '行八新备注'
    const op7 = ctx.patchItemField(row7, 'project_name')
    const op8 = ctx.patchItemField(row8, 'remark')

    first.reject(new Error('保存失败'))
    await op7
    expect(row7.project_name).toBe('原项目') // 失败行回到服务端快照
    expect(row8.remark).toBe('行八新备注') // 另一行未提交编辑不被覆盖
    expect(api.get).toHaveBeenCalledTimes(2) // 仅首次 load（详情+项目），不整体重载

    second.resolve({ data: { id: 8, remark: '行八新备注' } })
    await op8
    expect(row8.remark).toBe('行八新备注')
  })

  it('失败后重试加载可恢复详情', async () => {
    const ctx = mount()
    api.get.mockRejectedValueOnce(new Error('boom'))
    await ctx.load()
    expect(ctx.failed.value).toBe(true)
    api.get.mockImplementation(async (url) => (url.includes('/projects') ? { data: [] } : batchBody()))
    await ctx.load()
    expect(ctx.failed.value).toBe(false)
    expect(ctx.batch.value.items[0].project_name).toBe('原项目')
  })

  it('导出与下载使用长任务超时（导出 300s，下载 300s）', async () => {
    const ctx = mount()
    await ctx.load()
    api.post.mockResolvedValueOnce({ data: { projects: 1, project_zips: {}, zip_size: 1024 } })
    await ctx.doExport()
    expect(api.post.mock.calls[0][2]).toMatchObject({ timeout: 300000 })

    const { saveBlob } = await import('../src/utils/download.js')
    api.get.mockResolvedValueOnce({ data: new Blob(['zip']) })
    ctx.download()
    await vi.waitFor(() => expect(saveBlob).toHaveBeenCalled())
    const downloadCall = api.get.mock.calls.find(([url]) => url.includes('/download'))
    expect(downloadCall[1]).toMatchObject({ responseType: 'blob', timeout: 300000 })
  })
})
