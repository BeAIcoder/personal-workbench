// Task 6C fix round（integration-review M2）：
// 历史任务可能存着已不在 JobMode 白名单里的 mode（后端 SCH-01 只为执行侧兜底）。
// 编辑弹窗若原样回填脏值，用户不动模式就永远 422 —— 打开时归一为 readonly 并给出文案，
// 保存 payload 也用归一后的值。文案/行为一起锁。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { JOB_MODES, normalizeJobMode } from '../src/utils/jobs'

const jobApi = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  runNow: vi.fn(),
  runs: vi.fn(),
  actions: vi.fn(),
}))
const assistantApi = vi.hoisted(() => ({ agents: { list: vi.fn() } }))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))

vi.mock('../src/api', () => ({ jobApi, assistantApi }))

const JobsView = (await import('../src/views/JobsView.vue')).default

function mount() {
  return JobsView.setup({}, { expose: () => {}, emit: () => {} })
}

const dirtyRow = {
  id: 7,
  name: '历史任务',
  prompt: '汇总本周进项发票',
  agent_name: null,
  mode: 'fast',
  schedule_type: 'interval',
  interval_minutes: 60,
  daily_at: null,
  cron_expr: '',
  action: '',
  enabled: true,
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('normalizeJobMode', () => {
  it('白名单原样返回，非法/空值回退 readonly', () => {
    expect(JOB_MODES).toEqual(['readonly', 'standard', 'deep'])
    expect(normalizeJobMode('readonly')).toBe('readonly')
    expect(normalizeJobMode('deep')).toBe('deep')
    expect(normalizeJobMode('fast')).toBe('readonly')
    expect(normalizeJobMode('')).toBe('readonly')
    expect(normalizeJobMode(undefined)).toBe('readonly')
  })
})

describe('历史非法 mode 的任务可打开并可保存', () => {
  it('openEdit 回填 readonly 并给出历史值文案', () => {
    const ctx = mount()
    ctx.openEdit(dirtyRow)

    expect(ctx.form.mode).toBe('readonly')
    expect(ctx.dialog.modeFallback).toBe('fast')
  })

  it('脏 mode 任务不改模式直接保存：payload 提交 readonly 且不报 422', async () => {
    jobApi.update.mockResolvedValueOnce({ data: {} })
    jobApi.list.mockResolvedValueOnce({ data: [] })
    const ctx = mount()
    ctx.openEdit(dirtyRow)
    ctx.formRef.value = { validate: async () => true }

    await ctx.save()

    expect(jobApi.update).toHaveBeenCalledTimes(1)
    const [id, payload] = jobApi.update.mock.calls[0]
    expect(id).toBe(7)
    expect(payload.mode).toBe('readonly')
    expect(ctx.dialog.visible).toBe(false)
  })

  it('合法 mode 原样保留且不显示历史文案', () => {
    const ctx = mount()
    ctx.openEdit({ ...dirtyRow, mode: 'deep' })

    expect(ctx.form.mode).toBe('deep')
    expect(ctx.dialog.modeFallback).toBe('')
  })

  it('弹窗模板包含历史模式提示文案', () => {
    const src = readFileSync(new URL('../src/views/JobsView.vue', import.meta.url), 'utf8')
    expect(src).toContain('dialog.modeFallback')
    expect(src).toContain('已按只读咨询处理')
  })
})
