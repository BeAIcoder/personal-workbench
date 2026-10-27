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
      agents: {
        ...actual.assistantApi.agents,
        routeTest: vi.fn(),
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

describe('Task 2 路由预览状态', () => {
  function mountTeamPanel() {
    return AgentTeamPanel.setup({}, { expose: () => {}, emit: () => {} })
  }

  it('默认离线预览弱候选时显示待 AI 仲裁并明确需要仲裁', async () => {
    assistantApi.agents.routeTest.mockResolvedValue({
      data: {
        routed: 'realestate',
        matched_by: '关键词回退',
        matches: [
          {
            name: 'realestate',
            display_name: '商业地产管家',
            hits: ['安排'],
            score: 1,
            confidence: 'low',
            matched_terms: ['安排'],
          },
        ],
        needs_llm: true,
        normalized: '帮我安排roi',
        include_llm: false,
      },
    })
    const ctx = mountTeamPanel()
    ctx.routeText.value = '帮我安排ＲＯＩ'

    await ctx.runRouteTest()

    expect(assistantApi.agents.routeTest).toHaveBeenCalledWith('帮我安排ＲＯＩ', false)
    expect(ctx.routePreviewState.value).toEqual({ kind: 'pending', label: '待 AI 仲裁' })
    expect(ctx.routeNeedsText.value).toBe('需要 AI 仲裁：是')
  })

  it('高置信结果显示直接匹配并明确不需要仲裁', async () => {
    assistantApi.agents.routeTest.mockResolvedValue({
      data: {
        routed: 'leasing',
        matched_by: '关键词',
        matches: [
          {
            name: 'leasing',
            display_name: '招商专员',
            hits: ['招商'],
            score: 2,
            confidence: 'high',
            matched_terms: ['招商'],
          },
        ],
        needs_llm: false,
        normalized: '招商',
        include_llm: true,
      },
    })
    const ctx = mountTeamPanel()
    ctx.routeText.value = '招商'
    ctx.routeIncludeLlm.value = true

    await ctx.runRouteTest()

    expect(assistantApi.agents.routeTest).toHaveBeenCalledWith('招商', true)
    expect(ctx.routePreviewState.value).toEqual({ kind: 'direct', label: '直接匹配' })
    expect(ctx.routeNeedsText.value).toBe('需要 AI 仲裁：否')
  })

  it('显式 AI 仲裁失败回落默认时显示回退', async () => {
    assistantApi.agents.routeTest.mockResolvedValue({
      data: {
        routed: 'realestate',
        matched_by: '默认',
        matches: [],
        needs_llm: true,
        normalized: '今天天气怎么样',
        include_llm: true,
      },
    })
    const ctx = mountTeamPanel()
    ctx.routeText.value = '今天天气怎么样'
    ctx.routeIncludeLlm.value = true

    await ctx.runRouteTest()

    expect(assistantApi.agents.routeTest).toHaveBeenCalledWith('今天天气怎么样', true)
    expect(ctx.routePreviewState.value).toEqual({ kind: 'fallback', label: '回退' })
    expect(ctx.routeNeedsText.value).toBe('需要 AI 仲裁：是')
  })
})
