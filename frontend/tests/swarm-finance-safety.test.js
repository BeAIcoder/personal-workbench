// 财务安全回归测试（swarm 任务 frontend-finance-safety；swarm2 finance-ui-review 第二轮复核新增 S5 与两项契约）。
// 覆盖四类已确认缺陷：
//   S1 QuickAddView：账户/分类删除无确认框、无判忙，误点/连点即真实删除（QuickAddView.vue）
//   S2 TableListView：上传入仓默认 replace，整表覆盖已有表无明确确认，取消也同样发请求（TableListView.vue）
//   S3 BatchDetailView：批次加载失败时仍渲染详情壳，标题显示「批次详情 #undefined」（BatchDetailView.vue）
//   S4 可空数值直接 toFixed：StatsView/RunResultView 整页渲染崩溃，TransactionListView 金额列
//      显示 NaN，QuickAddView 金额空白（TransactionListView.vue / StatsView.vue / RunResultView.vue）
//   S5 QuickAddView.submit：缺重入守卫，在途再次提交会重复记账；TableListView 上传成功后
//      form.mode 未复位，再次打开上传弹窗仍默认破坏性 replace（swarm2 复核新增）
// 本仓库前端测试运行在 node 环境（vite.config.js test.environment=node，无 jsdom/挂载能力），
// 行为测试沿用既有约定：mock 生命周期钩子与 API 后直接驱动编译后的 setup()
// （同 e2e-bug-assistant-chat.test.js），模板结构断言沿用 interaction-submit-race.test.js 的源码约定。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    // 编译后的 SFC（dev 模板）会读 useSSRContext().modules：node 环境无 SSR 上下文，
    // 不 mock 会让真实 useSSRContext 返回 undefined 而在 mount 阶段崩溃
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
    onUnmounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'batch-1' }, query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))

vi.mock('../src/api', () => {
  const api = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }
  return { default: api, API_BASE: '/api' }
})

const { ElMessageBox } = await import('element-plus')
const { default: api } = await import('../src/api')
const QuickAddView = (await import('../src/modules/finance-ledger-personal/views/QuickAddView.vue')).default
const TransactionListView = (await import('../src/modules/finance-ledger-personal/views/TransactionListView.vue')).default
const StatsView = (await import('../src/modules/finance-ledger-personal/views/StatsView.vue')).default
const TableListView = (await import('../src/modules/finance-vault/views/TableListView.vue')).default
const BatchDetailView = (await import('../src/modules/finance-invoice/views/BatchDetailView.vue')).default
const RunResultView = (await import('../src/modules/finance-expense/views/RunResultView.vue')).default

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const readView = (rel) => readFileSync(join(root, 'src/modules', rel), 'utf8')
const templateOf = (rel) => readView(rel).split('<script setup>')[0]

function mount(Component, props = {}) {
  return Component.setup(props, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation((url) => {
    if (url.includes('/accounts')) return Promise.resolve({ data: [{ id: 'a1', name: '现金账户' }] })
    if (url.includes('/categories')) return Promise.resolve({ data: [{ id: 'c1', name: '餐饮', kind: 'expense' }] })
    if (url.includes('/transactions')) return Promise.resolve({ data: { items: [], total: 0 } })
    if (url.includes('/vault/tables')) {
      return Promise.resolve({
        data: { items: [{ table: 't_expense', rows: 100, columns: ['分类'], source: 'x.csv', updated_at: '2026-10-01' }] },
      })
    }
    if (url.includes('/invoice/batches/')) {
      return Promise.resolve({
        data: {
          id: 'batch-1',
          file_count: 2,
          status: 'parsed',
          parsed_count: 10,
          failed_count: 1,
          duplicate_count: 0,
          red_matched_count: 0,
          red_unmatched_count: 0,
          total_amount: 1234.5,
          error: null,
          items: [],
        },
      })
    }
    if (url.includes('/expense/runs/')) {
      return Promise.resolve({
        data: {
          run: { id: 'r1', status: 'success', original_filename: 'a.xlsx', total_rows: 2, matched_rows: 1, duration_sec: 1.2, created_at: null },
          stats: [{ category: '餐饮', amount: 100 }],
          rows: [{ row_no: 1, summary: 's', amount: 50, hit: true, category: '餐饮' }],
        },
      })
    }
    return Promise.resolve({ data: {} })
  })
  api.post.mockResolvedValue({ data: { rows: 10 } })
  api.patch.mockResolvedValue({ data: {} })
  api.delete.mockResolvedValue({ data: {} })
  ElMessageBox.confirm.mockResolvedValue('confirm')
})

describe('S1 QuickAddView：账户/分类删除需可取消确认且确认前判忙', () => {
  async function mountQuickAdd() {
    const ctx = mount(QuickAddView)
    await ctx.load()
    return ctx
  }

  it('删除账户前弹出可取消的确认框，确认后才发请求', async () => {
    const ctx = await mountQuickAdd()
    await ctx.deleteAccount({ id: 'a1', name: '现金账户' })
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    expect(ElMessageBox.confirm.mock.calls[0][0]).toContain('现金账户')
    expect(ElMessageBox.confirm.mock.calls[0][2]).toMatchObject({ type: 'warning' })
    expect(api.delete).toHaveBeenCalledWith('/finance/ledger/personal/accounts/a1')
  })

  it('删除分类前同样弹出确认框，确认后才发请求', async () => {
    const ctx = await mountQuickAdd()
    await ctx.deleteCategory({ id: 'c1', name: '餐饮' })
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    expect(ElMessageBox.confirm.mock.calls[0][0]).toContain('餐饮')
    expect(api.delete).toHaveBeenCalledWith('/finance/ledger/personal/categories/c1')
  })

  it('确认框取消时不发删除请求', async () => {
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')
    const ctx = await mountQuickAdd()
    await ctx.deleteAccount({ id: 'a1', name: '现金账户' })
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('删除请求失败被吞掉（拦截器已提示），不产生未捕获异常', async () => {
    api.delete.mockRejectedValueOnce(new Error('boom'))
    const ctx = await mountQuickAdd()
    await ctx.deleteAccount({ id: 'a1', name: '现金账户' })
    expect(api.delete).toHaveBeenCalledTimes(1)
  })

  it('确认框挂起时连点删除只开一个确认框（确认前判忙防连点）', async () => {
    ElMessageBox.confirm.mockReturnValue(new Promise(() => {})) // 对话框挂起：不确认也不取消
    const ctx = await mountQuickAdd()
    ctx.deleteAccount({ id: 'a1', name: '现金账户' })
    await ctx.deleteAccount({ id: 'a1', name: '现金账户' })
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
  })

  it('判忙锁在确认后才释放：删除完成后可再次删除', async () => {
    const ctx = await mountQuickAdd()
    await ctx.deleteAccount({ id: 'a1', name: '现金账户' })
    await ctx.deleteCategory({ id: 'c1', name: '餐饮' })
    expect(api.delete).toHaveBeenCalledTimes(2)
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(2)
  })

  it('本月流水金额列对 null/undefined 显示占位符，合法值保留两位小数', async () => {
    const ctx = await mountQuickAdd()
    ctx.todayItems.value = [
      { kind: 'expense', amount: null },
      { kind: 'income', amount: undefined },
      { kind: 'expense', amount: 12.5 },
    ]
    expect(ctx.fmtMoney(null)).toBe('—')
    expect(ctx.fmtMoney(undefined)).toBe('—')
    expect(ctx.fmtMoney('abc')).toBe('—')
    expect(ctx.fmtMoney(12.5)).toBe('12.50')
  })

  it('模板金额列改用 fmtMoney，不再直接调用 toFixed', () => {
    const tpl = templateOf('finance-ledger-personal/views/QuickAddView.vue')
    expect(tpl).toContain('fmtMoney(row.amount)')
    expect(tpl).not.toMatch(/row\.amount\??\.toFixed/)
  })
})

describe('S5 QuickAddView：记账提交带重入守卫，在途不重复记账', () => {
  async function mountQuickAddFilled() {
    const ctx = mount(QuickAddView)
    await ctx.load()
    ctx.form.amount = 9.9
    ctx.form.account_id = 'a1'
    ctx.form.category_id = 'c1'
    return ctx
  }

  it('表单不完整时只警告、不发记账请求（守卫不得误伤校验分支）', async () => {
    const ctx = await mountQuickAddFilled()
    ctx.form.amount = undefined
    await ctx.submit()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('提交在途时再次调用 submit 不会重复记账', async () => {
    const ctx = await mountQuickAddFilled()
    ctx.submit()
    await ctx.submit()
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post.mock.calls[0][0]).toBe('/finance/ledger/personal/transactions')
  })
})

describe('S4 TransactionListView：金额列 valueFormatter 空值显示占位符', () => {
  it('金额列对 null/undefined/非数字显示「—」，合法数字保留两位小数', () => {
    const ctx = mount(TransactionListView)
    const fmt = ctx.columnDefs.find((c) => c.field === 'amount').valueFormatter
    expect(fmt({ value: null })).toBe('—')
    expect(fmt({ value: undefined })).toBe('—')
    expect(fmt({ value: 'abc' })).toBe('—')
    expect(fmt({ value: 12.5 })).toBe('12.50')
    expect(fmt({ value: 0 })).toBe('0.00')
  })
})

describe('S4 StatsView：统计数值空值不崩溃，统一占位', () => {
  it('fmtMoney 对 null/undefined/非数字返回「—」，合法值两位小数', () => {
    const ctx = mount(StatsView)
    expect(ctx.fmtMoney(null)).toBe('—')
    expect(ctx.fmtMoney(undefined)).toBe('—')
    expect(ctx.fmtMoney('abc')).toBe('—')
    expect(ctx.fmtMoney(12.5)).toBe('12.50')
  })

  it('模板不再直接对 row/stats 数值调用 toFixed', () => {
    const tpl = templateOf('finance-ledger-personal/views/StatsView.vue')
    expect(tpl).toContain('fmtMoney(row.amount)')
    expect(tpl).toContain('fmtMoney(stats.income)')
    expect(tpl).toContain('fmtMoney(stats.expense)')
    expect(tpl).toContain('fmtMoney(stats.balance)')
    expect(tpl).not.toMatch(/\.toFixed\(/)
  })
})

describe('S2 TableListView：默认 append；replace 已有表必须明确确认，取消不发请求', () => {
  async function mountVault() {
    const ctx = mount(TableListView)
    await ctx.load()
    return ctx
  }

  it('上传模式默认为追加（append）而非整表重建', async () => {
    const ctx = await mountVault()
    expect(ctx.form.mode).toBe('append')
  })

  it('append 上传已有表不需要确认，直接发请求', async () => {
    const ctx = await mountVault()
    ctx.form.table = 't_expense'
    ctx.form.file = { name: 'a.csv' }
    ctx.form.mode = 'append'
    await ctx.submitUpload()
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('replace 已存在的表必须明确确认后才发请求', async () => {
    const ctx = await mountVault()
    ctx.form.table = 't_expense'
    ctx.form.file = { name: 'a.csv' }
    ctx.form.mode = 'replace'
    await ctx.submitUpload()
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    expect(ElMessageBox.confirm.mock.calls[0][0]).toContain('t_expense')
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post.mock.calls[0][0]).toContain('mode=replace')
  })

  it('replace 确认框取消时绝不发上传请求', async () => {
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')
    const ctx = await mountVault()
    ctx.form.table = 't_expense'
    ctx.form.file = { name: 'a.csv' }
    ctx.form.mode = 'replace'
    await ctx.submitUpload()
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    expect(api.post).not.toHaveBeenCalled()
  })

  it('replace 新表名（仓里不存在）不需要确认', async () => {
    const ctx = await mountVault()
    ctx.form.table = 't_new'
    ctx.form.file = { name: 'a.csv' }
    ctx.form.mode = 'replace'
    await ctx.submitUpload()
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('确认框挂起时连点「入仓」只开一个确认框', async () => {
    ElMessageBox.confirm.mockReturnValue(new Promise(() => {}))
    const ctx = await mountVault()
    ctx.form.table = 't_expense'
    ctx.form.file = { name: 'a.csv' }
    ctx.form.mode = 'replace'
    ctx.submitUpload()
    await ctx.submitUpload()
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
  })

  it('上传成功后模式复位为 append：下次打开弹窗不再默认破坏性 replace', async () => {
    const ctx = await mountVault()
    ctx.form.table = 't_expense'
    ctx.form.file = { name: 'a.csv' }
    ctx.form.mode = 'replace'
    await ctx.submitUpload()
    expect(ctx.form.mode).toBe('append')
  })
})

describe('S3 BatchDetailView：批次加载失败显示错误态而非 #undefined', () => {
  it('加载失败时进入 failed 状态（模板切到错误态，不再渲染 #undefined 详情头）', async () => {
    api.get.mockRejectedValueOnce(new Error('boom'))
    const ctx = mount(BatchDetailView)
    await ctx.load()
    expect(ctx.failed && ctx.failed.value).toBe(true)
  })

  it('加载成功后 failed 为 false，正常渲染详情', async () => {
    const ctx = mount(BatchDetailView)
    await ctx.load()
    expect(ctx.failed && ctx.failed.value).toBe(false)
    expect(ctx.batch.value.id).toBe('batch-1')
  })

  it('模板含加载失败分支，且「批次详情」头只在加载成功分支内', () => {
    const src = readView('finance-invoice/views/BatchDetailView.vue')
    expect(src).toContain('el-result')
    const tpl = src.split('<script setup>')[0]
    const guardIdx = tpl.indexOf('v-if="!failed"')
    const headIdx = tpl.indexOf('批次详情')
    expect(guardIdx).toBeGreaterThanOrEqual(0)
    expect(headIdx).toBeGreaterThan(guardIdx)
  })

  it('金额合计 fmtAmount 对 null/undefined/非数字返回占位符，合法值保留千分位两位小数', async () => {
    const ctx = mount(BatchDetailView)
    await ctx.load()
    expect(ctx.fmtAmount(null)).toBe('—')
    expect(ctx.fmtAmount(undefined)).toBe('—')
    expect(ctx.fmtAmount('abc')).toBe('—')
    expect(ctx.fmtAmount(1234.5)).toBe('1,234.50')
  })
})

describe('S4 RunResultView：空数值不崩溃，统一占位', () => {
  it('fmtMoney 对 null/undefined/非数字返回「—」，合法值两位小数', () => {
    const ctx = mount(RunResultView)
    expect(ctx.fmtMoney(null)).toBe('—')
    expect(ctx.fmtMoney(undefined)).toBe('—')
    expect(ctx.fmtMoney('abc')).toBe('—')
    expect(ctx.fmtMoney(12.5)).toBe('12.50')
  })

  it('明细金额列模板改用 fmtMoney，不再直接 toFixed', () => {
    const tpl = templateOf('finance-expense/views/RunResultView.vue')
    expect(tpl).toContain('fmtMoney(row.amount)')
    expect(tpl).not.toMatch(/\.toFixed\(/)
  })

  it('接口返回的 stats/rows 金额为 null 时 totalAmount 显示 0.00 而非 NaN', async () => {
    api.get.mockResolvedValueOnce({
      data: {
        run: { id: 'r1', status: 'success', original_filename: 'a.xlsx', total_rows: 2, matched_rows: 1, duration_sec: 1, created_at: null },
        stats: [{ category: '餐饮', amount: null }],
        rows: [{ row_no: 1, summary: 's', amount: null, hit: false, category: '未命中' }],
      },
    })
    const ctx = mount(RunResultView)
    await ctx.load()
    expect(ctx.totalAmount.value).toBe('0.00')
    expect(ctx.filteredRows.value).toHaveLength(1)
  })

  it('接口未返回 stats/rows 时不抛错，页面走空态', async () => {
    api.get.mockResolvedValueOnce({ data: { run: { id: 'r1', status: 'success', total_rows: 0, matched_rows: 0 } } })
    const ctx = mount(RunResultView)
    await ctx.load()
    expect(ctx.totalAmount.value).toBe('0.00')
    expect(ctx.filteredRows.value).toEqual([])
  })
})

describe('RunResultView：运行记录加载失败显示错误态', () => {
  it('加载失败时进入 failed 错误态，detail 保持 null（模板切 el-result，不渲染 undefined 详情）', async () => {
    api.get.mockRejectedValueOnce(new Error('boom'))
    const ctx = mount(RunResultView)
    await ctx.load()
    expect(ctx.failed.value).toBe(true)
    expect(ctx.detail.value).toBe(null)
  })
})
