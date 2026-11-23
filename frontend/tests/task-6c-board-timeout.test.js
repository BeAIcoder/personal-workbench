// Task 6C：看板「运行管线刷新」的前端超时预算（2026-11-17 改为异步运行 + 轮询）。
// 后端 finance_board/service.py（T5C）：单次尝试 = 两次 schtasks 查询（可用性 15s +
// 等待期间状态 15s）+ 触发 30s + max(计划通道 600s, 直接回退 600s) = 660s；
// 2 次尝试 + 重试 5s + 残留锁等待 60s → 最坏 1385s。
// 新契约：POST /finance/board/runs（短超时，仅登记）→ 轮询 GET /finance/board/runs/{id}
// （每请求 20s 上限）。只有旧后端 404 回退的同步 /run 才是单次阻塞请求，仍需覆盖预算。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  defaults: { baseURL: '/api' },
}))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../src/api', () => ({ default: api, API_BASE: '/api' }))

const { ElMessage } = await import('element-plus')

const BoardView = (await import('../src/modules/finance-board/views/BoardView.vue')).default

function mount() {
  return BoardView.setup({}, { expose: () => {}, emit: () => {} })
}

function backendBudgetMs() {
  const src = readFileSync(
    new URL('../../backend/app/modules/finance_board/service.py', import.meta.url),
    'utf8',
  )
  const num = (name) => {
    const value = Number(src.match(new RegExp(`^${name} = (\\d+)`, 'm'))?.[1])
    expect(value, `${name} 未在 finance_board/service.py 中定义`).toBeGreaterThan(0)
    return value
  }
  const attempts = num('_PIPELINE_ATTEMPTS')
  const retryDelay = num('_RETRY_DELAY_SECONDS')
  const lockWait = num('RETRY_LOCK_WAIT_SECONDS')
  // 单次尝试：两次 schtasks 查询（可用性 + 等待期间状态）+ 触发 + max(通道, 直接回退)
  const perAttempt = 2 * num('TASK_QUERY_TIMEOUT_SECONDS')
    + num('TASK_TRIGGER_TIMEOUT_SECONDS')
    + Math.max(num('TASK_CHANNEL_TIMEOUT'), num('DIRECT_ATTEMPT_BUDGET_SECONDS'))
  return attempts * perAttempt * 1000 + (attempts - 1) * (retryDelay + lockWait) * 1000
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('报表看板运行管线超时', () => {
  it('后端单次尝试 660s、两次尝试 + 重试/锁等待 = 1385s', () => {
    expect(backendBudgetMs()).toBe(1385000)
  })

  it('异步提交只等 60s：长预算不再压在单次请求上', async () => {
    api.post.mockResolvedValueOnce({ status: 202, data: { run_id: 'r1', status: 'running' } })
    api.get
      .mockResolvedValueOnce({ status: 200, data: { run_id: 'r1', status: 'ok', snapshot_id: 7, stamp: 's', elapsed_sec: 95 } })
      .mockResolvedValueOnce({ data: { items: [{ id: 7, status: 'ok', stamp: 's' }] } })
    const ctx = mount()

    await ctx.run()

    const [url, body, options] = api.post.mock.calls[0]
    expect(url).toBe('/finance/board/runs')
    expect(body).toEqual({})
    expect(options.timeout).toBeLessThanOrEqual(60000)
  })

  it('轮询请求同样短超时，不产生新的长挂起', async () => {
    api.post.mockResolvedValueOnce({ status: 202, data: { run_id: 'r2', status: 'running' } })
    api.get
      .mockResolvedValueOnce({ status: 200, data: { run_id: 'r2', status: 'ok', snapshot_id: 8, stamp: 't', elapsed_sec: 30 } })
      .mockResolvedValueOnce({ data: { items: [{ id: 8, status: 'ok', stamp: 't' }] } })
    const ctx = mount()

    await ctx.run()

    const [url, options] = api.get.mock.calls[0]
    expect(url).toBe('/finance/board/runs/r2')
    expect(options.timeout).toBeLessThanOrEqual(30000)
  })

  it('旧后端 404 回退同步 /run 时，超时覆盖预算且不超过预算 + 20%', async () => {
    const budget = backendBudgetMs()
    api.post
      .mockResolvedValueOnce({ status: 404, data: { detail: 'Not Found' } })
      .mockResolvedValueOnce({ data: { id: 7, stamp: '20261111_221500', elapsed_sec: 95, status: 'ok' } })
    api.get.mockResolvedValueOnce({ data: { items: [{ id: 7, status: 'ok', stamp: '20261111_221500' }] } })
    const ctx = mount()

    await ctx.run()

    expect(api.post).toHaveBeenCalledTimes(2)
    const [url, body, options] = api.post.mock.calls[1]
    expect(url).toBe('/finance/board/run')
    expect(body).toEqual({})
    expect(options.timeout).toBeGreaterThan(budget)
    expect(options.timeout).toBeLessThanOrEqual(Math.round(budget * 1.2))
  })

  it('T5 新增的快照字段（truncated/mismatch_total）不影响选择最新成功快照', async () => {
    api.get.mockResolvedValueOnce({
      data: {
        items: [
          { id: 9, status: 'ok', stamp: '20261111_230000', truncated: true, mismatch_total: 12 },
          { id: 8, status: 'failed', stamp: '20261111_220000' },
        ],
      },
    })
    const ctx = mount()

    await ctx.load()

    expect(ctx.selected.value).toBe(9)
    expect(ctx.items.value).toHaveLength(2)
  })

  it('T5 新增的 run.attempts 字段不影响成功路径（同步回退结果）', async () => {
    api.post
      .mockResolvedValueOnce({ status: 404, data: { detail: 'Not Found' } })
      .mockResolvedValueOnce({ data: { id: 7, stamp: '20261111_231500', elapsed_sec: 95, attempts: 2, status: 'ok' } })
    api.get.mockResolvedValueOnce({ data: { items: [{ id: 7, status: 'ok' }] } })
    const ctx = mount()

    await ctx.run()

    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('运行完成'))
    expect(ctx.selected.value).toBe(7)
  })
})
