// Task 6C：定时任务「立即运行」的前端超时必须大于服务端 Agent 预算。
// 后端 AGENT_JOB_TIMEOUT_SECONDS 从 scheduler.py 解析（与 POS/看板同口径），
// 后端调整预算时本契约直接报红。契约：timeout > 服务端预算。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'

const inst = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  interceptors: { response: { use: vi.fn() } },
  defaults: { baseURL: '/api' },
}))

vi.mock('axios', () => ({
  default: { create: () => inst, post: vi.fn() },
  create: () => inst,
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const { JOB_RUN_TIMEOUT_MS } = await import('../src/utils/jobs')
const { jobApi } = await import('../src/api')

function serverAgentBudgetMs() {
  const src = readFileSync(
    new URL('../../backend/app/scheduler.py', import.meta.url),
    'utf8',
  )
  const seconds = Number(src.match(/^AGENT_JOB_TIMEOUT_SECONDS = (\d+)/m)?.[1])
  expect(seconds, 'AGENT_JOB_TIMEOUT_SECONDS 未在 scheduler.py 中定义').toBeGreaterThan(0)
  return seconds * 1000
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('定时任务立即运行超时', () => {
  it('常量严格大于服务端 AGENT_JOB_TIMEOUT_SECONDS', () => {
    expect(typeof JOB_RUN_TIMEOUT_MS).toBe('number')
    expect(JOB_RUN_TIMEOUT_MS).toBeGreaterThan(serverAgentBudgetMs())
  })

  it('jobApi.runNow 实际带上该超时（不是 120s 旧值）', async () => {
    inst.post.mockResolvedValueOnce({ data: { status: 'ok' } })

    await jobApi.runNow(3)

    expect(inst.post).toHaveBeenCalledTimes(1)
    const [url, body, options] = inst.post.mock.calls[0]
    expect(url).toBe('/jobs/3/run')
    expect(body).toEqual({})
    expect(options.timeout).toBe(JOB_RUN_TIMEOUT_MS)
    expect(options.timeout).toBeGreaterThan(serverAgentBudgetMs())
  })
})
