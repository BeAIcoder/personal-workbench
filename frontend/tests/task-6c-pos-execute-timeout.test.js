// Task 6C 收尾：POS「执行」前端超时必须覆盖后端真实预算。
// 后端 _execute_run_locked 串行调用 2 个引擎脚本，每次 subprocess.run(timeout=STEP_TIMEOUT)，
// STEP_TIMEOUT=900s → 最坏 1800s。前端 600s 会在服务端仍在跑时先断，用户重按会重复触发。
// 契约直接从 backend/app/modules/finance_pos/service.py 解析预算，后端加步骤/调超时会直接报红。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))

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
vi.mock('../src/utils/download.js', () => ({ saveBlob: vi.fn(() => true), REVOKE_DELAY_MS: 10000 }))

const PosRunView = (await import('../src/modules/finance-pos/views/PosRunView.vue')).default

function backendBudget() {
  const src = readFileSync(
    new URL('../../backend/app/modules/finance_pos/service.py', import.meta.url),
    'utf8',
  )
  const stepTimeout = Number(src.match(/^STEP_TIMEOUT = (\d+)/m)?.[1])
  expect(stepTimeout).toBeGreaterThan(0)
  const start = src.indexOf('def _execute_run_locked(')
  expect(start).toBeGreaterThan(-1)
  const nextDef = src.indexOf('\ndef ', start + 1)
  const body = src.slice(start, nextDef > 0 ? nextDef : undefined)
  const steps = (body.match(/_run_script\(/g) || []).length
  return { stepTimeout, steps, budgetMs: stepTimeout * steps * 1000 }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.get.mockResolvedValue({ data: { items: [] } })
})

describe('POS 执行超时与后端预算契约', () => {
  it('后端预算为 2 个引擎步骤 × 900s = 1800s', () => {
    const { stepTimeout, steps, budgetMs } = backendBudget()
    expect(stepTimeout).toBe(900)
    expect(steps).toBe(2)
    expect(budgetMs).toBe(1800000)
  })

  it('execute() 的超时严格大于后端两步预算', async () => {
    const { budgetMs } = backendBudget()
    api.post.mockResolvedValueOnce({ data: { status: 'ok', artifacts: ['POS项目台账.xlsx'] } })
    const ctx = PosRunView.setup({}, { expose: () => {}, emit: () => {} })

    await ctx.execute({ id: 7, status: 'uploaded' })

    expect(api.post).toHaveBeenCalledTimes(1)
    const [url, , options] = api.post.mock.calls[0]
    expect(url).toBe('/finance/pos/runs/7/execute')
    expect(options.timeout).toBeGreaterThan(budgetMs)
  })
})
