// assistant-settings 区域 E2E 探测（AS-PROV-011）回归测试。
// 「快速预设」下拉只走 applyPreset：此前仅填 base_url/协议，未设置候选模型，
// 导致「候选模型」表单项不出现、保存后模型行数量为 0；「从预设填充」路径正常。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { assistantApi } from '../src/api'
import AgentTeamPanel from '../src/components/assistant/AgentTeamPanel.vue'
import SettingsDrawer from '../src/components/assistant/SettingsDrawer.vue'

// node 环境无 DOM：mock 消息组件与 API，直接驱动编译后的 setup()
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }), onMounted: () => {} }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    assistantApi: {
      ...actual.assistantApi,
      models: {
        ...actual.assistantApi.models,
        list: vi.fn(),
      },
      providers: {
        ...actual.assistantApi.providers,
        save: vi.fn(),
        saveModel: vi.fn(),
      },
    },
  }
})

const PRESETS = [
  {
    name: 'deepseek',
    label: 'DeepSeek 官方',
    protocol: 'openai',
    base_url: 'https://api.deepseek.com/v1',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    note: '官方直连',
    docs_url: 'https://platform.deepseek.com/api-docs/',
  },
  {
    name: 'siliconflow',
    label: '硅基流动 SiliconFlow',
    protocol: 'openai',
    base_url: 'https://api.siliconflow.cn/v1',
    models: ['Qwen/Qwen3.5-122B-A10B'],
    note: '国内直连',
    docs_url: '',
  },
]

function mountDrawer() {
  const emit = vi.fn()
  const ctx = SettingsDrawer.setup(
    { visible: false, providers: [], presets: PRESETS, teamConfig: {}, initialTab: 'providers' },
    { expose: () => {}, emit },
  )
  return { ctx, emit }
}

// 模拟 el-select：v-model 先写入 preset，再触发 @change
function selectQuickPreset(ctx, name) {
  ctx.provDialog.preset = name
  ctx.applyPreset(name)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AS-PROV-011 快速预设候选模型', () => {
  it('选择快速预设：填充 base_url/协议并带出全部候选模型', () => {
    const { ctx } = mountDrawer()
    ctx.openProviderForm()
    selectQuickPreset(ctx, 'deepseek')

    expect(ctx.provDialog.base_url).toBe('https://api.deepseek.com/v1')
    expect(ctx.provDialog.protocol).toBe('openai')
    expect(ctx.provDialog.name).toBe('DeepSeek 官方')
    expect(ctx.presetCandidateModels.value).toEqual(['deepseek-chat', 'deepseek-reasoner'])
    expect(ctx.provDialog.presetModels).toEqual(['deepseek-chat', 'deepseek-reasoner'])
    // 模型表单的下拉候选与供应商表单同源
    expect(ctx.presetModels.value).toEqual(['deepseek-chat', 'deepseek-reasoner'])
  })

  it('切换快速预设：候选模型整体替换而不是累积', () => {
    const { ctx } = mountDrawer()
    ctx.openProviderForm()
    selectQuickPreset(ctx, 'deepseek')
    selectQuickPreset(ctx, 'siliconflow')

    expect(ctx.presetCandidateModels.value).toEqual(['Qwen/Qwen3.5-122B-A10B'])
    expect(ctx.provDialog.presetModels).toEqual(['Qwen/Qwen3.5-122B-A10B'])
  })

  it('保存新供应商：逐条添加候选模型', async () => {
    const { ctx, emit } = mountDrawer()
    ctx.openProviderForm()
    selectQuickPreset(ctx, 'deepseek')
    ctx.provDialog.name = '预设探测供应商'
    assistantApi.providers.save.mockResolvedValue({ data: { provider: { id: 42 } } })
    assistantApi.providers.saveModel.mockResolvedValue({ data: {} })

    await ctx.saveProvider()

    expect(assistantApi.providers.saveModel).toHaveBeenCalledTimes(2)
    expect(assistantApi.providers.saveModel).toHaveBeenNthCalledWith(1, 42, {
      model: 'deepseek-chat',
      context_size: 128000,
      max_tokens: 2048,
    })
    expect(assistantApi.providers.saveModel).toHaveBeenNthCalledWith(2, 42, {
      model: 'deepseek-reasoner',
      context_size: 128000,
      max_tokens: 2048,
    })
    expect(emit).toHaveBeenCalledWith('refresh')
    expect(ctx.provDialog.visible).toBe(false)
  })

  it('用户取消勾选的候选模型不添加', async () => {
    const { ctx } = mountDrawer()
    ctx.openProviderForm()
    selectQuickPreset(ctx, 'deepseek')
    ctx.provDialog.presetModels = ['deepseek-chat']
    ctx.provDialog.name = '只留一个模型'
    assistantApi.providers.save.mockResolvedValue({ data: { provider: { id: 43 } } })
    assistantApi.providers.saveModel.mockResolvedValue({ data: {} })

    await ctx.saveProvider()

    expect(assistantApi.providers.saveModel).toHaveBeenCalledTimes(1)
    expect(assistantApi.providers.saveModel).toHaveBeenCalledWith(43, expect.objectContaining({ model: 'deepseek-chat' }))
  })

  it('编辑已有供应商不重复添加候选模型', async () => {
    const { ctx } = mountDrawer()
    ctx.editProvider({ id: 7, name: '既有供应商', protocol: 'openai', base_url: 'https://x/v1', enabled: true })
    selectQuickPreset(ctx, 'deepseek')
    assistantApi.providers.save.mockResolvedValue({ data: { provider: { id: 7 } } })

    await ctx.saveProvider()

    expect(assistantApi.providers.saveModel).not.toHaveBeenCalled()
  })
})

describe('AS-AGENT-009 专家绑定模型候选刷新', () => {
  it('打开专家表单前重新拉取模型列表（新增模型无需重开抽屉即可绑定）', async () => {
    assistantApi.models.list.mockResolvedValue({ data: { models: [{ id: 9, label: 'bind-model' }] } })
    const ctx = AgentTeamPanel.setup({}, { expose: () => {}, emit: () => {} })

    await ctx.openForm()

    expect(assistantApi.models.list).toHaveBeenCalledTimes(1)
    expect(ctx.models.value).toEqual([{ id: 9, label: 'bind-model' }])
    expect(ctx.form.visible).toBe(true)
  })
})
