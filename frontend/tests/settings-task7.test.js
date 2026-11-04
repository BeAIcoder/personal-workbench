// Task 7 / FE L-126、L-136、L-140：AI 设置页闭环。
// - 快速预设必须回填 preset_key/models_url（否则「同步模型」拿不到适配器）；
//   但用户已填的值不得被覆盖。
// - 工作参数接 routing_model_id 与 memory_inject_max（0 是合法值，不能被 ?? 吃掉）。
// - 探测/实测忙态重入要有提示，不能静默 return。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive } from 'vue'

const assistantApi = vi.hoisted(() => ({
  config: { saveGlobals: vi.fn(), test: vi.fn(), get: vi.fn(), discoverSkills: vi.fn(), discoverPlugins: vi.fn() },
  providers: { save: vi.fn(), remove: vi.fn(), saveModel: vi.fn(), removeModel: vi.fn(), activate: vi.fn() },
  models: { list: vi.fn(), resetOverrides: vi.fn() },
  agents: { routeTest: vi.fn() },
}))
const probeApi = vi.hoisted(() => ({
  probeReasoning: vi.fn(),
  probeContext: vi.fn(),
  probeOutput: vi.fn(),
  probeAll: vi.fn(),
  getProbe: vi.fn(),
}))
const message = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }))
const messageBox = vi.hoisted(() => ({ confirm: vi.fn() }))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }), onMounted: () => {} }
})
vi.mock('element-plus', () => ({ ElMessage: message, ElMessageBox: messageBox }))
vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    assistantApi,
    modelProbeApi: probeApi,
    providerPresetsApi: { list: vi.fn().mockResolvedValue({ data: { presets: [] } }) },
  }
})

const SettingsDrawer = (await import('../src/components/assistant/SettingsDrawer.vue')).default

const PRESETS = [
  {
    name: 'deepseek',
    label: 'DeepSeek 官方',
    protocol: 'openai',
    base_url: 'https://api.deepseek.com/v1',
    preset_key: 'deepseek',
    models_url: 'https://api.deepseek.com/models',
    models: ['deepseek-chat'],
  },
  {
    name: 'siliconflow',
    label: '硅基流动',
    protocol: 'openai',
    base_url: 'https://api.siliconflow.cn/v1',
    preset_key: 'dashscope',
    models_url: 'https://api.siliconflow.cn/v1/models',
    models: ['Qwen/Qwen3.5-122B-A10B'],
  },
]

const PROVIDERS = [{
  id: 1,
  name: 'DeepSeek',
  enabled: true,
  protocol: 'openai',
  base_url: 'https://api.deepseek.com/v1',
  models: [
    { id: 11, model: 'deepseek-chat', label: 'DeepSeek · deepseek-chat', enabled: true },
    { id: 12, model: 'deepseek-reasoner', label: 'DeepSeek · deepseek-reasoner', enabled: false },
  ],
}, {
  id: 2,
  name: '停用供应商',
  enabled: false,
  models: [{ id: 21, model: 'hidden', enabled: true }],
}]

function mount(teamConfig = {}) {
  const props = {
    visible: false,
    providers: PROVIDERS,
    presets: PRESETS,
    teamConfig: {
      routing_model_id: 11,
      memory_inject_max: 0,
      max_iters: 4,
      enable_memory: true,
      history_inject: 8,
      ...teamConfig,
    },
    initialTab: 'globals',
  }
  const emit = vi.fn()
  const ctx = SettingsDrawer.setup(props, { expose: () => {}, emit })
  return { ctx, emit, props }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.clearAllMocks()
  messageBox.confirm.mockResolvedValue('confirm')
  assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: {} } })
})

describe('快速预设回填同步适配器字段', () => {
  it('选预设后 preset_key/models_url 一并填入（同步模型才有适配器）', () => {
    const { ctx } = mount()
    ctx.openProviderForm()
    ctx.provDialog.preset = 'deepseek'
    ctx.applyPreset('deepseek')
    expect(ctx.provDialog.preset_key).toBe('deepseek')
    expect(ctx.provDialog.models_url).toBe('https://api.deepseek.com/models')
    expect(ctx.provDialog.base_url).toBe('https://api.deepseek.com/v1')
  })

  it('用户已填的 preset_key/models_url 不被预设覆盖', () => {
    const { ctx } = mount()
    ctx.openProviderForm()
    ctx.provDialog.preset_key = 'custom_key'
    ctx.provDialog.models_url = 'https://custom.example.com/models'
    ctx.provDialog.preset = 'deepseek'
    ctx.applyPreset('deepseek')
    expect(ctx.provDialog.preset_key).toBe('custom_key')
    expect(ctx.provDialog.models_url).toBe('https://custom.example.com/models')
  })

  it('「从预设填充」同样带回两个字段且不覆盖已填值', () => {
    const { ctx } = mount()
    ctx.openProviderForm()
    ctx.applyPresetFill('siliconflow')
    expect(ctx.provDialog.preset_key).toBe('dashscope')
    expect(ctx.provDialog.models_url).toBe('https://api.siliconflow.cn/v1/models')
    ctx.applyPresetFill('deepseek')
    expect(ctx.provDialog.preset_key).toBe('deepseek')
  })
})

describe('工作参数：路由模型与记忆预算', () => {
  it('打开设置时回填 teamConfig（0 是合法的记忆预算，不回退默认）', () => {
    const { ctx } = mount()
    ctx.applyTeamConfig()
    expect(ctx.globalsForm.routing_model_id).toBe(11)
    expect(ctx.globalsForm.memory_inject_max).toBe(0)
    expect(ctx.globalsForm.max_iters).toBe(4)
    expect(ctx.globalsForm.history_inject).toBe(8)
  })

  it('保存时透传 routing_model_id 与 memory_inject_max', async () => {
    const { ctx } = mount()
    ctx.applyTeamConfig()
    ctx.globalsForm.routing_model_id = 12
    ctx.globalsForm.memory_inject_max = 5
    await ctx.saveGlobals()
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith(expect.objectContaining({
      routing_model_id: 12,
      memory_inject_max: 5,
    }))
  })

  it('清空路由模型时发送 null（跟随全局激活模型）', async () => {
    const { ctx } = mount()
    ctx.applyTeamConfig()
    ctx.globalsForm.routing_model_id = ''
    await ctx.saveGlobals()
    const payload = assistantApi.config.saveGlobals.mock.calls[0][0]
    expect(payload.routing_model_id).toBeNull()
  })

  it('路由模型候选只含启用供应商的启用模型，带供应商前缀', () => {
    const { ctx } = mount()
    const options = ctx.routingModelOptions.value
    expect(options.map((o) => o.id)).toEqual([11])
    expect(options[0].label).toContain('DeepSeek')
  })
})

describe('探测与实测重入提示', () => {
  it('探测进行中再次点击：提示忙态且不重复发请求', async () => {
    const { ctx } = mount()
    const pending = deferred()
    probeApi.probeReasoning.mockReturnValueOnce(pending.promise)
    const model = { id: 11, model: 'deepseek-chat' }

    const first = ctx.runProbe(model, 'reasoning')
    await ctx.runProbe(model, 'context')
    expect(message.warning).toHaveBeenCalledWith(expect.stringContaining('探测'))
    expect(probeApi.probeReasoning).toHaveBeenCalledTimes(1)
    expect(probeApi.probeContext).not.toHaveBeenCalled()

    pending.resolve({ data: { probe: {} } })
    await first
    expect(ctx.probingId.value).toBeNull()
  })

  it('连通实测忙时提示，不静默丢点击', async () => {
    const { ctx } = mount()
    ctx.testingId.value = '1:text'
    await ctx.testProvider(PROVIDERS[0], 'text')
    expect(message.warning).toHaveBeenCalledWith(expect.stringContaining('实测'))
    expect(assistantApi.config.test).not.toHaveBeenCalled()
  })
})

describe('探测跨入口互斥（I-1）', () => {
  const batchProvider = {
    id: 5,
    name: '批量供应商',
    enabled: true,
    models: [
      { id: 101, model: 'batch-a', enabled: true },
      { id: 102, model: 'batch-b', enabled: true },
    ],
  }

  it('批量探测进行中：单模型探测被拒，且批量继续跑完不被中途解锁', async () => {
    const { ctx } = mount()
    const first = deferred()
    probeApi.probeAll.mockReturnValueOnce(first.promise).mockResolvedValueOnce({ data: {} })

    const batch = ctx.runProbeAllModels(batchProvider)
    await vi.waitFor(() => expect(ctx.probingProviderId.value).toBe(5))
    expect(ctx.probingId.value).toBe('101:all')

    await ctx.runProbe({ id: 11, model: 'deepseek-chat' }, 'reasoning')
    expect(message.warning).toHaveBeenCalledWith(expect.stringContaining('探测'))
    expect(probeApi.probeReasoning).not.toHaveBeenCalled()

    first.resolve({ data: {} })
    await batch
    expect(probeApi.probeAll).toHaveBeenCalledTimes(2)
    expect(ctx.probingProviderId.value).toBeNull()
    expect(ctx.probingId.value).toBeNull()
  })

  it('单模型探测进行中：批量探测被拒', async () => {
    const { ctx } = mount()
    const pending = deferred()
    probeApi.probeReasoning.mockReturnValueOnce(pending.promise)
    const single = ctx.runProbe({ id: 11, model: 'deepseek-chat' }, 'reasoning')

    await ctx.runProbeAllModels(batchProvider)
    expect(message.warning).toHaveBeenCalledWith(expect.stringContaining('单模型探测'))
    expect(probeApi.probeAll).not.toHaveBeenCalled()

    pending.resolve({ data: { probe: {} } })
    await single
  })

  it('单个模型「全部」探测也受批量忙态门控', async () => {
    const { ctx } = mount()
    ctx.probingProviderId.value = 5
    await ctx.runProbeAll({ id: 11, model: 'deepseek-chat' })
    expect(message.warning).toHaveBeenCalledWith(expect.stringContaining('探测'))
    expect(probeApi.probeAll).not.toHaveBeenCalled()
  })

  it('模板按钮在任一探测忙态下禁用（跨入口共用 probeBusy）', () => {
    const { ctx } = mount()
    expect(ctx.probeBusy.value).toBe(false)
    ctx.probingId.value = '11:reasoning'
    expect(ctx.probeBusy.value).toBe(true)
    ctx.probingId.value = null
    ctx.probingProviderId.value = 5
    expect(ctx.probeBusy.value).toBe(true)
  })
})

describe('配置未就绪保护（M6）', () => {
  it('teamConfig 尚未加载时禁用保存并提示，不把默认值写回后端', async () => {
    const ctx = SettingsDrawer.setup(
      { visible: false, providers: PROVIDERS, presets: PRESETS, teamConfig: {}, initialTab: 'globals' },
      { expose: () => {}, emit: vi.fn() },
    )
    ctx.applyTeamConfig()
    expect(ctx.configReady.value).toBe(false)
    await ctx.saveGlobals()
    expect(message.warning).toHaveBeenCalledWith(expect.stringContaining('配置'))
    expect(assistantApi.config.saveGlobals).not.toHaveBeenCalled()
  })

  it('已加载配置后可正常保存', () => {
    const { ctx } = mount({ max_iters: 4 })
    ctx.applyTeamConfig()
    expect(ctx.configReady.value).toBe(true)
  })
})

describe('配置到达后回填（M6 复审）', () => {
  it('打开后配置才到达：先回填再保存，不把默认值写回', async () => {
    const props = reactive({
      visible: false,
      providers: PROVIDERS,
      presets: PRESETS,
      teamConfig: {},
      initialTab: 'globals',
    })
    const ctx = SettingsDrawer.setup(props, { expose: () => {}, emit: vi.fn() })
    ctx.globalsForm.max_iters = 9 // 模拟打开前的默认值，配置到达后必须被真实值覆盖

    props.visible = true
    await nextTick()
    expect(ctx.configReady.value).toBe(false)
    expect(ctx.globalsForm.max_iters).toBe(9) // 未就绪不得用默认值回填/保存

    props.teamConfig = {
      max_iters: 4,
      history_inject: 8,
      memory_inject_max: 2,
      routing_model_id: 11,
    }
    await nextTick()
    expect(ctx.configReady.value).toBe(true)
    expect(ctx.globalsForm.max_iters).toBe(4)
    expect(ctx.globalsForm.memory_inject_max).toBe(2)
    expect(ctx.globalsForm.routing_model_id).toBe(11)

    await ctx.saveGlobals()
    const payload = assistantApi.config.saveGlobals.mock.calls[0][0]
    expect(payload).toMatchObject({ max_iters: 4, history_inject: 8, memory_inject_max: 2, routing_model_id: 11 })
  })
})

describe('配置刷新不覆盖用户编辑（R-1）', () => {
  it('编辑 max_iters=9 后 teamConfig 刷新，9 保留且保存值为 9', async () => {
    const props = reactive({
      visible: false,
      providers: PROVIDERS,
      presets: PRESETS,
      teamConfig: { max_iters: 4, history_inject: 8, memory_inject_max: 2, routing_model_id: 11 },
      initialTab: 'globals',
    })
    const ctx = SettingsDrawer.setup(props, { expose: () => {}, emit: vi.fn() })
    props.visible = true
    await nextTick()
    expect(ctx.globalsForm.max_iters).toBe(4)

    ctx.globalsForm.max_iters = 9 // 用户未保存编辑
    props.teamConfig = { max_iters: 5, history_inject: 8, memory_inject_max: 2, routing_model_id: 11 }
    await nextTick()
    expect(ctx.globalsForm.max_iters).toBe(9) // 刷新不得覆盖 dirty 编辑

    await ctx.saveGlobals()
    expect(assistantApi.config.saveGlobals.mock.calls[0][0].max_iters).toBe(9)
  })
})

describe('设置抽屉关闭重开（Task 8 补充）', () => {
  it('关闭复位回填状态，重开后重新加载最新配置且不保留上次未保存编辑', async () => {
    const props = reactive({
      visible: false,
      providers: PROVIDERS,
      presets: PRESETS,
      teamConfig: { max_iters: 4, history_inject: 8, memory_inject_max: 2, routing_model_id: 11 },
      initialTab: 'globals',
    })
    const ctx = SettingsDrawer.setup(props, { expose: () => {}, emit: vi.fn() })

    props.visible = true
    await nextTick()
    expect(ctx.globalsForm.max_iters).toBe(4)
    ctx.globalsForm.max_iters = 9 // 未保存编辑

    props.visible = false
    await nextTick()
    // 关闭期间的配置刷新不得写入表单（下次打开才回填）
    props.teamConfig = { max_iters: 6, history_inject: 8, memory_inject_max: 2, routing_model_id: 11 }
    await nextTick()
    expect(ctx.globalsForm.max_iters).toBe(9)

    props.visible = true
    await nextTick()
    expect(ctx.globalsForm.max_iters).toBe(6)
    expect(ctx.configReady.value).toBe(true)
  })
})

describe('记忆预算后端默认值（M7）', () => {
  it('teamConfig 缺失该字段时按后端默认 3 展示，而不是 0', () => {
    const { ctx } = mount({ memory_inject_max: undefined })
    ctx.applyTeamConfig()
    expect(ctx.globalsForm.memory_inject_max).toBe(3)
  })

  it('显式 0 仍表示不注入，不被默认值覆盖', () => {
    const { ctx } = mount({ memory_inject_max: 0 })
    ctx.applyTeamConfig()
    expect(ctx.globalsForm.memory_inject_max).toBe(0)
  })
})
