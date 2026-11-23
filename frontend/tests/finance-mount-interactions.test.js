// @vitest-environment jsdom
// 关键交互的真实挂载测试（#8）：jsdom + @vue/test-utils 渲染真实组件与 Element Plus，
// 通过 DOM 事件驱动，而不是只读 .vue 源码做静态断言。
// 覆盖：看板异步运行/轮询/同步回退、数据池清洗弹窗、POS 打包下载。
import './jsdom-setup.js'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))

const saveBlob = vi.hoisted(() => vi.fn(() => true))

vi.mock('../src/api', () => ({ default: api, API_BASE: '/api' }))
vi.mock('../src/utils/download.js', () => ({
  saveBlob,
  openPrintWindow: vi.fn(() => ({ close: vi.fn() })),
  showPdfInWindow: vi.fn(() => 'blob:pdf'),
  REVOKE_DELAY_MS: 10000,
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))
vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
  }
})

const { ElMessage } = await import('element-plus')
const BoardView = (await import('../src/modules/finance-board/views/BoardView.vue')).default
const TablesPanel = (await import('../src/modules/finance-vault/views/parts/TablesPanel.vue')).default
const PosRunView = (await import('../src/modules/finance-pos/views/PosRunView.vue')).default

const GLOBAL = {
  plugins: [ElementPlus],
  // 非被测组件用桩替换：帮助链接/助手按钮/模板下载/目录选择器需要路由或全局 store
  stubs: { HelpLink: true, AskAiButton: true, TemplateLinks: true, DirectoryPicker: true },
}

function clickButton(wrapper, label) {
  const btn = wrapper.findAll('button').find((b) => b.text().trim() === label)
  expect(btn, `未找到按钮「${label}」`).toBeTruthy()
  return btn
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockReset()
  api.post.mockReset()
})

describe('报表看板：202 提交 + 轮询运行状态', () => {
  function routeBoard({ snapshots, polls, submit }) {
    api.get.mockImplementation((url) => {
      if (url === '/finance/board/snapshots') return Promise.resolve({ data: { items: snapshots } })
      if (url.startsWith('/finance/board/runs/')) {
        const next = polls.shift()
        if (!next) throw new Error(`多余的轮询请求：${url}`)
        return Promise.resolve(next)
      }
      throw new Error(`未预期的 GET ${url}`)
    })
    api.post.mockImplementation((url) => {
      const next = submit[url]
      if (!next) throw new Error(`未预期的 POST ${url}`)
      return Promise.resolve(next)
    })
  }

  it('提交 202 后轮询到 ok：展示进度、刷新列表并选中新快照', async () => {
    routeBoard({
      snapshots: [{ id: 42, stamp: '20261117_180000', status: 'ok' }],
      polls: [
        { status: 200, data: { run_id: 'r1', status: 'running' } },
        { status: 200, data: { run_id: 'r1', status: 'ok', snapshot_id: 42, stamp: '20261117_180000', elapsed_sec: 88 } },
      ],
      submit: { '/finance/board/runs': { status: 202, data: { run_id: 'r1', status: 'running' } } },
    })
    const wrapper = mount(BoardView, { global: GLOBAL })
    await flushPromises()

    const running = wrapper.vm.run()
    await flushPromises()
    expect(wrapper.text()).toContain('管线运行中')

    await running
    expect(api.post).toHaveBeenCalledWith(
      '/finance/board/runs',
      {},
      expect.objectContaining({ timeout: 60000 }),
    )
    expect(api.get).toHaveBeenCalledWith(
      '/finance/board/runs/r1',
      expect.objectContaining({ timeout: 20000 }),
    )
    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('运行完成'))
    expect(wrapper.vm.selected).toBe(42)
    expect(wrapper.text()).not.toContain('管线运行中')
  })

  it('后端无 /runs（404）时静默回退同步 /run，不弹错误提示', async () => {
    routeBoard({
      snapshots: [{ id: 7, stamp: '20261117_190000', status: 'ok' }],
      polls: [],
      submit: {
        '/finance/board/runs': { status: 404, data: { detail: 'Not Found' } },
        '/finance/board/run': { data: { id: 7, stamp: '20261117_190000', status: 'ok', elapsed_sec: 95 } },
      },
    })
    const wrapper = mount(BoardView, { global: GLOBAL })
    await flushPromises()

    await wrapper.vm.run()

    expect(api.post).toHaveBeenNthCalledWith(
      2,
      '/finance/board/run',
      {},
      expect.objectContaining({ timeout: 1500000 }),
    )
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('运行完成'))
    expect(wrapper.vm.selected).toBe(7)
  })

  it('轮询记录失效（404）时提示并保留快照列表，不假报成功', async () => {
    routeBoard({
      snapshots: [{ id: 43, stamp: '20261117_181500', status: 'failed', log_tail: 'boom' }],
      polls: [{ status: 404, data: { detail: 'run not found' } }],
      submit: { '/finance/board/runs': { status: 202, data: { run_id: 'gone', status: 'running' } } },
    })
    const wrapper = mount(BoardView, { global: GLOBAL })
    await flushPromises()

    await wrapper.vm.run()

    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('运行记录已失效'))
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(wrapper.vm.items).toHaveLength(1)
  })
})

describe('数据池清洗弹窗', () => {
  const rows = [{ table: 't_expense', source: '费用.xlsx', rows: 10, columns: [{ name: '金额' }] }]

  beforeEach(() => {
    api.get.mockImplementation((url) => {
      if (url === '/finance/vault/tables') return Promise.resolve({ data: { items: rows } })
      throw new Error(`未预期的 GET ${url}`)
    })
  })

  it('点「清洗」打开弹窗，预览清洗按结果表名提交并展示报告', async () => {
    api.post.mockResolvedValueOnce({
      data: { rows_in: 10, rows_kept: 9, ops: [{ op: 'dedupe_rows', removed: 1 }], warnings: [], samples: [] },
    })
    const wrapper = mount(TablesPanel, { global: GLOBAL })
    await flushPromises()

    await clickButton(wrapper, '清洗').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('清洗：t_expense')
    expect(wrapper.text()).toContain('结果表名')
    expect(wrapper.vm.cleanResultTable).toBe('t_expense_clean')

    await clickButton(wrapper, '预览清洗').trigger('click')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith(
      '/finance/vault/tables/t_expense/clean/preview',
      expect.objectContaining({ target_table: 't_expense_clean' }),
    )
    expect(wrapper.text()).toContain('清洗报告：t_expense')
    expect(wrapper.text()).toContain('保留行数')
  })

  it('结果表名与源表相同时拒绝提交（预览/应用均禁用）', async () => {
    const wrapper = mount(TablesPanel, { global: GLOBAL })
    await flushPromises()
    await clickButton(wrapper, '清洗').trigger('click')
    await flushPromises()

    wrapper.vm.cleanResultTable = 't_expense'
    await flushPromises()

    expect(wrapper.text()).toContain('结果表名不能与源表相同')
    const preview = clickButton(wrapper, '预览清洗')
    expect(preview.element.disabled).toBe(true)
    await preview.trigger('click')
    expect(api.post).not.toHaveBeenCalled()
  })
})

describe('POS 运行产物打包下载', () => {
  beforeEach(() => {
    api.get.mockImplementation((url) => {
      if (url === '/finance/pos/runs') {
        return Promise.resolve({
          data: {
            items: [
              { id: 7, status: 'ok', input_files: [], artifacts: ['a.xlsx'], elapsed_sec: 3, created_at: '2026-11-17 18:00' },
            ],
          },
        })
      }
      if (url === '/finance/pos/vouchers/config') return Promise.resolve({ data: {} })
      if (url === '/finance/pos/vouchers') return Promise.resolve({ data: { items: [] } })
      if (url === '/finance/pos/runs/7/download') {
        return Promise.resolve({ data: new Blob(['zip'], { type: 'application/zip' }) })
      }
      throw new Error(`未预期的 GET ${url}`)
    })
  })

  it('点「打包下载」走 axios blob + saveBlob 并显式落文件名', async () => {
    const wrapper = mount(PosRunView, { global: GLOBAL })
    await flushPromises()

    await clickButton(wrapper, '打包下载').trigger('click')
    await flushPromises()

    expect(api.get).toHaveBeenCalledWith(
      '/finance/pos/runs/7/download',
      expect.objectContaining({ responseType: 'blob' }),
    )
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), 'POS对账_运行7.zip')
  })
})
