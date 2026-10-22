// swarm 任务 frontend-settings-knowledge 聚焦回归：
// SettingsDrawer / KnowledgeView 的异常收口（失败不伪成功、锁复位）、
// 删除模型可取消确认（取消零请求）、忙态防连点、
// 目录检测未保存路径时「先保存再检测」（配合后端只允许已保存目录）。
// node 环境无 DOM：mock 消息组件与 API，直接驱动编译后的 setup()。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { assistantApi, modelProbeApi } from '../src/api'
import SettingsDrawer from '../src/components/assistant/SettingsDrawer.vue'
import KnowledgeView from '../src/views/KnowledgeView.vue'

// vitest 的 SSR transform 会往 setup 里注入 useSSRContext()；KnowledgeView 还用了
// onMounted/onBeforeUnmount，node 环境下一并 stub 才能脱离组件实例调用
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
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    // KnowledgeView 用默认导出（裸 axios 实例）
    default: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
    assistantApi: {
      ...actual.assistantApi,
      config: {
        ...actual.assistantApi.config,
        saveGlobals: vi.fn(),
        discoverSkills: vi.fn(),
        discoverPlugins: vi.fn(),
        test: vi.fn(),
      },
      providers: {
        ...actual.assistantApi.providers,
        save: vi.fn(),
        saveModel: vi.fn(),
        removeModel: vi.fn(),
      },
    },
    modelProbeApi: {
      ...actual.modelProbeApi,
      probeAll: vi.fn(),
    },
  }
})

function mountDrawer(teamConfig = {}) {
  const emit = vi.fn()
  const ctx = SettingsDrawer.setup(
    { visible: false, providers: [], presets: [], teamConfig, initialTab: 'providers' },
    { expose: () => {}, emit },
  )
  return { ctx, emit }
}

function mountKnowledge() {
  const ctx = KnowledgeView.setup({}, { expose: () => {}, emit: () => {} })
  return { ctx }
}

// 让出宏任务：等 mock 的 confirm 解析与占锁推进到可观测状态
const tick = () => new Promise((r) => setTimeout(r, 0))
// 慢请求：10ms 后才 resolve，用于观测在途忙态
const slow = (value) => () => new Promise((r) => setTimeout(() => r(value), 10))

beforeEach(() => {
  // resetAllMocks 一并清掉 once 队列与工厂实现，保证每个测试显式自给自足
  vi.resetAllMocks()
})

describe('删除模型：可取消确认与取消零请求', () => {
  it('用户取消确认时不发起删除请求、不刷新', async () => {
    const { ctx, emit } = mountDrawer()
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')
    await ctx.removeModel({ id: 3, name: '供应商甲' }, { id: 7, model: 'deepseek-chat' })

    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(assistantApi.providers.removeModel).not.toHaveBeenCalled()
    expect(emit).not.toHaveBeenCalled()
    expect(ctx.removingModelId.value).toBeNull()
  })

  it('确认后发起删除请求并刷新列表', async () => {
    const { ctx, emit } = mountDrawer()
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    assistantApi.providers.removeModel.mockResolvedValueOnce({ data: {} })

    await ctx.removeModel({ id: 3, name: '供应商甲' }, { id: 7, model: 'deepseek-chat' })

    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(assistantApi.providers.removeModel).toHaveBeenCalledWith(3, 7)
    expect(emit).toHaveBeenCalledWith('refresh')
    expect(ctx.removingModelId.value).toBeNull()
  })

  it('删除请求进行中防连点：不发起第二次删除', async () => {
    const { ctx } = mountDrawer()
    ElMessageBox.confirm.mockResolvedValue('confirm')
    assistantApi.providers.removeModel.mockImplementationOnce(slow({ data: {} }))

    const first = ctx.removeModel({ id: 3, name: 'p' }, { id: 7, model: 'm' })
    await tick()
    const second = ctx.removeModel({ id: 3, name: 'p' }, { id: 8, model: 'm2' })
    await second

    expect(assistantApi.providers.removeModel).toHaveBeenCalledTimes(1)
    await first
    expect(ctx.removingModelId.value).toBeNull()
  })

  it('删除失败：不伪成功，锁复位', async () => {
    const { ctx, emit } = mountDrawer()
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    assistantApi.providers.removeModel.mockRejectedValueOnce(new Error('boom'))

    await ctx.removeModel({ id: 3, name: 'p' }, { id: 7, model: 'm' })

    expect(assistantApi.providers.removeModel).toHaveBeenCalledWith(3, 7)
    expect(ctx.removingModelId.value).toBeNull()
    // 与既有口径一致：失败也刷新，拉取后端真实模型列表
    expect(emit).toHaveBeenCalledWith('refresh')
  })
})

describe('异常收口：失败不伪成功且锁复位', () => {
  it('saveGlobals 失败：不提示成功、不关闭抽屉、锁复位', async () => {
    const { ctx, emit } = mountDrawer()
    assistantApi.config.saveGlobals.mockRejectedValueOnce(new Error('boom'))

    await ctx.saveGlobals()

    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(emit).not.toHaveBeenCalledWith('update:visible', false)
    expect(emit).not.toHaveBeenCalledWith('refresh')
    expect(ctx.savingCfg.value).toBe(false)
  })

  it('saveGlobals 成功：提示、刷新并关闭抽屉', async () => {
    const { ctx, emit } = mountDrawer()
    assistantApi.config.saveGlobals.mockResolvedValueOnce({ data: {} })

    await ctx.saveGlobals()

    expect(ElMessage.success).toHaveBeenCalledWith('工作参数已保存并生效')
    expect(emit).toHaveBeenCalledWith('refresh')
    expect(emit).toHaveBeenCalledWith('update:visible', false)
    expect(ctx.savingCfg.value).toBe(false)
  })

  it('saveGlobals 忙态防连点：保存中不发起第二次保存', async () => {
    const { ctx } = mountDrawer()
    assistantApi.config.saveGlobals.mockImplementationOnce(slow({ data: {} }))

    const first = ctx.saveGlobals()
    await tick()
    expect(ctx.savingCfg.value).toBe(true)
    const second = ctx.saveGlobals()
    await second

    expect(assistantApi.config.saveGlobals).toHaveBeenCalledTimes(1)
    await first
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ctx.savingCfg.value).toBe(false)
  })

  it('saveProvider 失败：对话框保持打开、锁复位', async () => {
    const { ctx, emit } = mountDrawer()
    ctx.openProviderForm()
    ctx.provDialog.name = '新供应商'
    assistantApi.providers.save.mockRejectedValueOnce(new Error('boom'))

    await ctx.saveProvider()

    expect(ctx.provDialog.visible).toBe(true)
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(emit).not.toHaveBeenCalledWith('refresh')
    expect(ctx.provDialog.saving).toBe(false)
  })

  it('saveProvider 忙态防连点：保存中不发起第二次保存', async () => {
    const { ctx } = mountDrawer()
    ctx.openProviderForm()
    ctx.provDialog.name = '新供应商'
    assistantApi.providers.save.mockImplementationOnce(slow({ data: { provider: { id: 1 } } }))
    // 旧实现无守卫时第二次调用会落到持久桩上正常返回，失败原因即为「重复发起」
    assistantApi.providers.save.mockResolvedValue({ data: { provider: { id: 1 } } })

    const first = ctx.saveProvider()
    await tick()
    expect(ctx.provDialog.saving).toBe(true)
    const second = ctx.saveProvider()
    await second

    expect(assistantApi.providers.save).toHaveBeenCalledTimes(1)
    await first
    expect(ctx.provDialog.saving).toBe(false)
  })

  it('saveModel 失败：对话框保持打开、锁复位', async () => {
    const { ctx, emit } = mountDrawer()
    ctx.openModelForm({ id: 3 }, { id: 7, model: 'deepseek-chat' })
    assistantApi.providers.saveModel.mockRejectedValueOnce(new Error('boom'))

    await ctx.saveModel()

    expect(ctx.modelDialog.visible).toBe(true)
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(emit).not.toHaveBeenCalledWith('refresh')
    expect(ctx.modelDialog.saving).toBe(false)
  })

  it('saveModel 忙态防连点：保存中不发起第二次保存', async () => {
    const { ctx } = mountDrawer()
    ctx.openModelForm({ id: 3 }, { id: 7, model: 'deepseek-chat' })
    assistantApi.providers.saveModel.mockImplementationOnce(slow({ data: {} }))

    const first = ctx.saveModel()
    await tick()
    expect(ctx.modelDialog.saving).toBe(true)
    const second = ctx.saveModel()
    await second

    expect(assistantApi.providers.saveModel).toHaveBeenCalledTimes(1)
    await first
    expect(ctx.modelDialog.saving).toBe(false)
  })

  it('testProvider 失败：锁复位、不写检测结果', async () => {
    const { ctx } = mountDrawer()
    assistantApi.config.test.mockRejectedValueOnce(new Error('boom'))

    await ctx.testProvider({ id: 1, name: 'p' }, 'text')

    expect(ctx.provTestResult.value[1]).toBeUndefined()
    expect(ctx.testingProvId.value).toBeNull()
  })

  it('testProvider 忙态防连点：测试进行中不发起第二个测试', async () => {
    const { ctx } = mountDrawer()
    const okData = {
      data: {
        text: { ok: true, latency_ms: 5, reply: 'hi' },
        tools: { ok: true, latency_ms: 5, tool_calls: ['x'] },
        image: { ok: true, latency_ms: 5, reply: 'img' },
      },
    }
    assistantApi.config.test.mockImplementationOnce(slow(okData))
    assistantApi.config.test.mockResolvedValue(okData)

    const first = ctx.testProvider({ id: 1, name: 'p' }, 'text')
    await tick()
    expect(ctx.testingProvId.value).toBe('1:text')
    const second = ctx.testProvider({ id: 1, name: 'p' }, 'tools')
    await second

    expect(assistantApi.config.test).toHaveBeenCalledTimes(1)
    await first
    expect(ctx.testingProvId.value).toBeNull()
  })

  it('discoverSkillsCount 失败：锁复位、不清空结果也不写新结果', async () => {
    const { ctx } = mountDrawer({ skills_dir: 'D:\\saved' })
    ctx.globalsForm.skills_dir = 'D:\\saved'
    assistantApi.config.discoverSkills.mockRejectedValueOnce(new Error('boom'))

    await ctx.discoverSkillsCount()

    expect(ctx.skillsFound.value).toBeNull()
    expect(ctx.discoveringSkills.value).toBe(false)
  })

  it('discoverSkillsCount 忙态防连点：检测中不发起第二次检测', async () => {
    const { ctx } = mountDrawer({ skills_dir: 'D:\\saved' })
    ctx.globalsForm.skills_dir = 'D:\\saved'
    assistantApi.config.discoverSkills.mockImplementationOnce(slow({ data: { count: 1, names: ['a'] } }))
    assistantApi.config.discoverSkills.mockResolvedValue({ data: { count: 1, names: ['a'] } })

    const first = ctx.discoverSkillsCount()
    await tick()
    expect(ctx.discoveringSkills.value).toBe(true)
    const second = ctx.discoverSkillsCount()
    await second

    expect(assistantApi.config.discoverSkills).toHaveBeenCalledTimes(1)
    await first
    expect(ctx.discoveringSkills.value).toBe(false)
  })

  it('discoverPluginsCount 失败：锁复位、不写结果', async () => {
    const { ctx } = mountDrawer({ plugins_dir: 'D:\\saved' })
    ctx.globalsForm.plugins_dir = 'D:\\saved'
    assistantApi.config.discoverPlugins.mockRejectedValueOnce(new Error('boom'))

    await ctx.discoverPluginsCount()

    expect(ctx.pluginsFound.value).toBeNull()
    expect(ctx.discoveringPlugins.value).toBe(false)
  })

  it('discoverPluginsCount 忙态防连点：检测中不发起第二次检测', async () => {
    const { ctx } = mountDrawer({ plugins_dir: 'D:\\saved' })
    ctx.globalsForm.plugins_dir = 'D:\\saved'
    assistantApi.config.discoverPlugins.mockImplementationOnce(slow({ data: { loadable: 1, plugins: [] } }))
    assistantApi.config.discoverPlugins.mockResolvedValue({ data: { loadable: 1, plugins: [] } })

    const first = ctx.discoverPluginsCount()
    await tick()
    expect(ctx.discoveringPlugins.value).toBe(true)
    const second = ctx.discoverPluginsCount()
    await second

    expect(assistantApi.config.discoverPlugins).toHaveBeenCalledTimes(1)
    await first
    expect(ctx.discoveringPlugins.value).toBe(false)
  })

  it('runProbeAllModels 忙态防连点：批量进行中不开始第二批', async () => {
    const { ctx } = mountDrawer()
    ElMessageBox.confirm.mockResolvedValue('confirm')
    modelProbeApi.probeAll.mockImplementation(slow({ data: { result: {}, probe: {} } }))

    const first = ctx.runProbeAllModels({ id: 5, name: 'p', models: [{ id: 21, model: 'a', enabled: true }] })
    await tick()
    expect(ctx.probingProviderId.value).toBe(5)
    const second = ctx.runProbeAllModels({ id: 5, name: 'p', models: [{ id: 21, model: 'a', enabled: true }] })
    await second

    expect(modelProbeApi.probeAll).toHaveBeenCalledTimes(1)
    await first
  })

  it('runProbeAllModels 全部失败：给出失败汇总、不提示成功、锁复位', async () => {
    const { ctx } = mountDrawer()
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    modelProbeApi.probeAll.mockRejectedValue(new Error('boom'))

    await ctx.runProbeAllModels({
      id: 5,
      name: 'p',
      models: [{ id: 21, model: 'a', enabled: true }, { id: 22, model: 'b', enabled: true }],
    })

    expect(modelProbeApi.probeAll).toHaveBeenCalledTimes(2)
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('失败 2 个'))
    expect(ctx.probingProviderId.value).toBeNull()
    expect(ctx.probingId.value).toBeNull()
  })

  it('doRebuild 失败：不提示「索引已重建」、不刷新统计、锁复位', async () => {
    const { ctx } = mountKnowledge()
    api.post.mockRejectedValueOnce(new Error('boom'))

    await ctx.doRebuild()

    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(api.get).not.toHaveBeenCalled()
    expect(ctx.rebuilding.value).toBe(false)
  })

  it('doRebuild 成功：提示并刷新统计', async () => {
    const { ctx } = mountKnowledge()
    api.post.mockResolvedValueOnce({ data: {} })
    api.get.mockResolvedValueOnce({ data: { notes_indexed: 3, chunks: 9, vocabulary: 5 } })

    await ctx.doRebuild()

    expect(ElMessage.success).toHaveBeenCalledWith('索引已重建')
    expect(ctx.stats).toMatchObject({ notes_indexed: 3, chunks: 9, vocabulary: 5 })
    expect(ctx.rebuilding.value).toBe(false)
  })

  it('doRebuild 忙态防连点：重建中不发起第二次重建', async () => {
    const { ctx } = mountKnowledge()
    api.post.mockImplementationOnce(slow({ data: {} }))

    const first = ctx.doRebuild()
    await tick()
    expect(ctx.rebuilding.value).toBe(true)
    const second = ctx.doRebuild()
    await second

    expect(api.post).toHaveBeenCalledTimes(1)
    await first
    expect(ctx.rebuilding.value).toBe(false)
  })
})

describe('目录检测：未保存路径先保存再检测（后端只允许已保存目录）', () => {
  it('技能池路径未保存：先弹确认；用户取消则零请求', async () => {
    const { ctx, emit } = mountDrawer() // teamConfig {} → 已保存目录为空
    ctx.globalsForm.skills_dir = 'D:\\skills\\pool'
    // 旧实现无保存门：若直发检测也应能返回，确保失败原因是「缺确认」而非 mock 未就绪
    assistantApi.config.discoverSkills.mockResolvedValue({ data: { count: 0, names: [] } })
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')

    await ctx.discoverSkillsCount()

    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(assistantApi.config.saveGlobals).not.toHaveBeenCalled()
    expect(assistantApi.config.discoverSkills).not.toHaveBeenCalled()
    expect(ctx.discoveringSkills.value).toBe(false)
    expect(emit).not.toHaveBeenCalled()
  })

  it('技能池路径未保存：确认后先保存工作参数再检测，且不关闭抽屉', async () => {
    const { ctx, emit } = mountDrawer()
    ctx.globalsForm.skills_dir = 'D:\\skills\\pool'
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    assistantApi.config.saveGlobals.mockResolvedValueOnce({ data: {} })
    assistantApi.config.discoverSkills.mockResolvedValueOnce({ data: { count: 2, names: ['a', 'b'] } })

    await ctx.discoverSkillsCount()

    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledTimes(1)
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith(
      expect.objectContaining({ skills_dir: 'D:\\skills\\pool' }),
    )
    expect(assistantApi.config.discoverSkills).toHaveBeenCalledWith('D:\\skills\\pool')
    expect(ctx.skillsFound.value).toBe(2)
    expect(emit).toHaveBeenCalledWith('refresh')
    expect(emit).not.toHaveBeenCalledWith('update:visible', false)
  })

  it('技能池路径未保存：保存失败则不发起检测', async () => {
    const { ctx } = mountDrawer()
    ctx.globalsForm.skills_dir = 'D:\\skills\\pool'
    assistantApi.config.discoverSkills.mockResolvedValue({ data: { count: 0, names: [] } })
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    assistantApi.config.saveGlobals.mockRejectedValueOnce(new Error('boom'))

    await ctx.discoverSkillsCount()

    expect(assistantApi.config.saveGlobals).toHaveBeenCalledTimes(1)
    expect(assistantApi.config.discoverSkills).not.toHaveBeenCalled()
    expect(ctx.discoveringSkills.value).toBe(false)
  })

  it('技能池路径与已保存一致：直接检测，不弹确认', async () => {
    const { ctx } = mountDrawer({ skills_dir: 'D:\\skills\\pool' })
    ctx.globalsForm.skills_dir = 'D:\\skills\\pool'
    assistantApi.config.discoverSkills.mockResolvedValueOnce({ data: { count: 0, names: [] } })

    await ctx.discoverSkillsCount()

    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(assistantApi.config.discoverSkills).toHaveBeenCalledWith('D:\\skills\\pool')
    expect(ctx.skillsFound.value).toBe(0)
  })

  it('技能池目录为空：提示填写，不发起任何请求', async () => {
    const { ctx } = mountDrawer()
    ctx.globalsForm.skills_dir = '   '

    await ctx.discoverSkillsCount()

    expect(ElMessage.warning).toHaveBeenCalledWith('请先填写技能池目录')
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(assistantApi.config.discoverSkills).not.toHaveBeenCalled()
  })

  it('插件路径未保存：先弹确认；用户取消则零请求', async () => {
    const { ctx, emit } = mountDrawer()
    ctx.globalsForm.plugins_dir = 'D:\\plugins'
    assistantApi.config.discoverPlugins.mockResolvedValue({ data: { loadable: 0, plugins: [] } })
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')

    await ctx.discoverPluginsCount()

    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(assistantApi.config.saveGlobals).not.toHaveBeenCalled()
    expect(assistantApi.config.discoverPlugins).not.toHaveBeenCalled()
    expect(ctx.discoveringPlugins.value).toBe(false)
    expect(emit).not.toHaveBeenCalled()
  })

  it('插件路径未保存：确认后先保存工作参数再检测，且不关闭抽屉', async () => {
    const { ctx, emit } = mountDrawer()
    ctx.globalsForm.plugins_dir = 'D:\\plugins'
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    assistantApi.config.saveGlobals.mockResolvedValueOnce({ data: {} })
    assistantApi.config.discoverPlugins.mockResolvedValueOnce({
      data: { loadable: 1, plugins: [{ name: 'x', ok: true, tools: ['t'] }] },
    })

    await ctx.discoverPluginsCount()

    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith(
      expect.objectContaining({ plugins_dir: 'D:\\plugins' }),
    )
    expect(assistantApi.config.discoverPlugins).toHaveBeenCalledWith('D:\\plugins')
    expect(ctx.pluginsFound.value).toMatchObject({ loadable: 1 })
    expect(emit).toHaveBeenCalledWith('refresh')
    expect(emit).not.toHaveBeenCalledWith('update:visible', false)
  })

  it('插件路径未保存：保存失败则不发起检测', async () => {
    const { ctx } = mountDrawer()
    ctx.globalsForm.plugins_dir = 'D:\\plugins'
    assistantApi.config.discoverPlugins.mockResolvedValue({ data: { loadable: 0, plugins: [] } })
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    assistantApi.config.saveGlobals.mockRejectedValueOnce(new Error('boom'))

    await ctx.discoverPluginsCount()

    expect(assistantApi.config.saveGlobals).toHaveBeenCalledTimes(1)
    expect(assistantApi.config.discoverPlugins).not.toHaveBeenCalled()
    expect(ctx.discoveringPlugins.value).toBe(false)
  })

  it('插件路径与已保存一致：直接检测，不弹确认', async () => {
    const { ctx } = mountDrawer({ plugins_dir: 'D:\\plugins' })
    ctx.globalsForm.plugins_dir = 'D:\\plugins'
    assistantApi.config.discoverPlugins.mockResolvedValueOnce({ data: { loadable: 0, plugins: [] } })

    await ctx.discoverPluginsCount()

    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(assistantApi.config.discoverPlugins).toHaveBeenCalledWith('D:\\plugins')
  })

  it('插件目录为空：提示填写，不发起任何请求', async () => {
    const { ctx } = mountDrawer()
    ctx.globalsForm.plugins_dir = ''

    await ctx.discoverPluginsCount()

    expect(ElMessage.warning).toHaveBeenCalledWith('请先填写插件目录')
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(assistantApi.config.discoverPlugins).not.toHaveBeenCalled()
  })
})
