import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assistantApi, modelProbeApi, providerPresetsApi } from '../src/api'
import SettingsDrawer, {
  THINKING_LEVEL_OPTIONS,
  contextResultMessage,
  effectiveModelText,
  formatK,
  outputResultMessage,
  parseLevels,
  probeAllSummary,
  probeAtShort,
  probeResultOf,
  probeStateOf,
  providerKeyBadge,
  reasoningResultMessage,
  thinkingBadge,
} from '../src/components/assistant/SettingsDrawer.vue'

// node 环境无 DOM：mock 掉消息组件与探测/预设 API，直接驱动编译后的 setup()
// vitest 的 SSR transform 会往 setup 里注入 useSSRContext()，需一并 stub 才能脱离组件实例调用
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }) }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    modelProbeApi: {
      probeReasoning: vi.fn(),
      probeContext: vi.fn(),
      probeOutput: vi.fn(),
      probeAll: vi.fn(),
      getProbe: vi.fn(),
    },
    providerPresetsApi: {
      list: vi.fn(),
      reload: vi.fn(),
    },
    assistantApi: {
      ...actual.assistantApi,
      providers: {
        ...actual.assistantApi.providers,
        save: vi.fn(),
        saveModel: vi.fn(),
      },
    },
  }
})

function mountDrawer() {
  const emit = vi.fn()
  const ctx = SettingsDrawer.setup(
    { visible: false, providers: [], presets: [], teamConfig: {}, initialTab: 'providers' },
    { expose: () => {}, emit },
  )
  return { ctx, emit }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('思考能力徽章', () => {
  it('supports_thinking=1 → 绿色「思考✓」', () => {
    const badge = thinkingBadge(1)
    expect(badge.text).toBe('思考✓')
    expect(badge.type).toBe('success')
    expect(badge.dashed).toBe(false)
  })

  it('supports_thinking=0 → 灰色「思考✗」', () => {
    const badge = thinkingBadge(0)
    expect(badge.text).toBe('思考✗')
    expect(badge.type).toBe('info')
    expect(badge.dashed).toBe(false)
  })

  it('supports_thinking=-1 或缺省 → 灰色虚线「未探测」', () => {
    expect(thinkingBadge(-1)).toMatchObject({ text: '未探测', type: 'info', dashed: true })
    expect(thinkingBadge(undefined).text).toBe('未探测')
    expect(thinkingBadge(null).dashed).toBe(true)
  })
})

describe('token 数 k/m 格式化', () => {
  it('按 1024 取整，超过 1024k 转 m', () => {
    expect(formatK(131072)).toBe('128k')
    expect(formatK(32768)).toBe('32k')
    expect(formatK(4096)).toBe('4k')
    expect(formatK(200000)).toBe('195k')
    expect(formatK(1048576)).toBe('1m')
    expect(formatK(1500000)).toBe('1.4m')
  })

  it('0 / 空 / 非法值不显示', () => {
    expect(formatK(0)).toBe('')
    expect(formatK(null)).toBe('')
    expect(formatK(undefined)).toBe('')
    expect(formatK('')).toBe('')
    expect(formatK(-1)).toBe('')
    expect(formatK('abc')).toBe('')
  })
})

describe('thinking_levels 解析', () => {
  it('JSON 字符串 → 数组', () => {
    expect(parseLevels('["low","medium","high"]')).toEqual(['low', 'medium', 'high'])
  })

  it('数组原样过滤非字符串/空串', () => {
    expect(parseLevels(['low', '', null, 'high'])).toEqual(['low', 'high'])
  })

  it('容错：空值与坏 JSON', () => {
    expect(parseLevels('')).toEqual([])
    expect(parseLevels('   ')).toEqual([])
    expect(parseLevels(null)).toEqual([])
    expect(parseLevels(undefined)).toEqual([])
    expect(parseLevels('not-json')).toEqual([])
    expect(parseLevels('{"a":1}')).toEqual([])
  })

  it('容错：逗号分隔字符串', () => {
    expect(parseLevels('low, high')).toEqual(['low', 'high'])
  })
})

describe('思考级别选项', () => {
  it('与后端约定的六档一致', () => {
    expect(THINKING_LEVEL_OPTIONS).toEqual(['none', 'low', 'medium', 'high', 'xhigh', 'max'])
  })
})

describe('探测响应解包', () => {
  it('后端实际形态 {model_id, result, probe}', () => {
    const data = { model_id: 1, result: { effective_context: 131072 }, probe: { probed_context: 131072 } }
    expect(probeResultOf(data)).toEqual({ effective_context: 131072 })
    expect(probeStateOf(data)).toEqual({ probed_context: 131072 })
  })

  it('兼容契约定平的平铺形态', () => {
    const data = { model_id: 1, probed_context: 131072, supports_thinking: 1 }
    expect(probeStateOf(data).probed_context).toBe(131072)
    expect(probeStateOf(data).supports_thinking).toBe(1)
    expect(probeResultOf(data).probed_context).toBe(131072)
  })

  it('空值兜底', () => {
    expect(probeStateOf(null)).toEqual({})
    expect(probeResultOf(undefined)).toEqual({})
    expect(probeStateOf('x')).toEqual({})
  })
})

describe('探测结论文案', () => {
  it('思考探测：支持时列出级别', () => {
    const data = { result: { thinking_levels: ['low', 'medium', 'high'] }, probe: { supports_thinking: 1 } }
    expect(reasoningResultMessage(data)).toEqual({ level: 'success', text: '支持思考：low/medium/high' })
  })

  it('思考探测：支持但无级别 / 不支持', () => {
    expect(reasoningResultMessage({ result: {}, probe: { supports_thinking: 1 } })).toEqual({ level: 'success', text: '支持思考：未给出级别' })
    expect(reasoningResultMessage({ result: {}, probe: { supports_thinking: 0 } })).toEqual({ level: 'success', text: '模型不支持思考' })
  })

  it('思考探测：anthropic 跳过 → warning，error 字段 → error', () => {
    expect(reasoningResultMessage({ result: { skipped: 'anthropic 协议暂不支持自动探测' }, probe: {} })).toEqual({
      level: 'warning',
      text: 'anthropic 协议暂不支持自动探测',
    })
    expect(reasoningResultMessage({ result: { error: '厂商不可达或全部请求失败，未更新探测结论' }, probe: {} })).toEqual({
      level: 'error',
      text: '厂商不可达或全部请求失败，未更新探测结论',
    })
  })

  it('上下文 / 输出探测：优先取分步详情，其次状态', () => {
    expect(contextResultMessage({ result: { effective_context: 131072 }, probe: { probed_context: 131072 } })).toEqual({ level: 'success', text: '上下文有效 131072' })
    expect(contextResultMessage({ result: {}, probe: { probed_context: 64000 } })).toEqual({ level: 'success', text: '上下文有效 64000' })
    expect(contextResultMessage({ result: {}, probe: {} })).toEqual({ level: 'success', text: '上下文有效 0' })
    expect(outputResultMessage({ result: { effective_max_output: 32768 }, probe: {} })).toEqual({ level: 'success', text: '最大输出 32768' })
    expect(contextResultMessage({ result: { error: '连接超时' }, probe: {} })).toEqual({ level: 'error', text: '连接超时' })
    expect(outputResultMessage({ result: { skipped: 'anthropic 协议暂不支持自动探测' }, probe: {} }).level).toBe('warning')
  })

  it('全量探测摘要包含三个维度', () => {
    const data = {
      result: { reasoning: {}, context: {}, output: {}, probed_at: '2026-10-12T09:30:00' },
      probe: { supports_thinking: 1, thinking_levels: ['low', 'high'], probed_context: 131072, probed_max_output: 32768 },
    }
    const summary = probeAllSummary(data)
    expect(summary).toContain('思考 low/high')
    expect(summary).toContain('ctx 131072')
    expect(summary).toContain('out 32768')
    expect(probeAllSummary({ result: {}, probe: { supports_thinking: 0, probed_context: 0, probed_max_output: 0 } })).toContain('思考 不支持')
    expect(probeAllSummary({ result: {}, probe: {} })).toContain('思考 未探测')
    expect(probeAllSummary(null)).toContain('思考 未探测')
  })
})

describe('探测时间展示', () => {
  it('ISO / 空格时间只保留到分钟', () => {
    expect(probeAtShort('2026-10-12T09:30:45')).toBe('2026-10-12 09:30')
    expect(probeAtShort('2026-10-12 09:30:45')).toBe('2026-10-12 09:30')
  })

  it('空值与非标准值兜底', () => {
    expect(probeAtShort('')).toBe('')
    expect(probeAtShort(null)).toBe('')
    expect(probeAtShort('昨天')).toBe('昨天')
  })
})

describe('当前生效模型与供应商状态（P0-2 / P1-7）', () => {
  it('生效模型文案：供应商激活模型标注来源，env 兜底明示', () => {
    expect(effectiveModelText({ model: 'glm-5.2', provider_name: '智谱' })).toBe('当前生效模型：glm-5.2（来源：供应商「智谱」）')
    expect(effectiveModelText({ model: 'Qwen/Qwen3.5', provider_name: 'env兜底' })).toBe('当前生效模型：Qwen/Qwen3.5（来源：.env 兜底）')
  })

  it('未配置时引导去添加供应商', () => {
    expect(effectiveModelText({})).toContain('未配置')
    expect(effectiveModelText(null)).toContain('未配置')
  })

  it('供应商 Key 徽标：停用不显示「已配置」', () => {
    expect(providerKeyBadge({ enabled: true, api_key_set: true })).toEqual({ text: '已配置', type: 'success' })
    expect(providerKeyBadge({ enabled: true, api_key_set: false })).toEqual({ text: '未配置 Key', type: 'info' })
    expect(providerKeyBadge({ enabled: false, api_key_set: true })).toEqual({ text: '已停用', type: 'info' })
    expect(providerKeyBadge(null)).toEqual({ text: '已停用', type: 'info' })
  })
})

describe('探测交互（组件 setup 级）', () => {
  it('思考探测成功：就地更新徽章并提示结论', async () => {
    const { ctx, emit } = mountDrawer()
    const m = { id: 7, model: 'deepseek-reasoner', supports_thinking: -1, probed_context: 0 }
    modelProbeApi.probeReasoning.mockResolvedValue({
      data: {
        model_id: 7,
        result: { levels: { low: 'supported' }, thinking_levels: ['low', 'medium'], requests: 4 },
        probe: { supports_thinking: 1, thinking_levels: ['low', 'medium'], probed_context: 0, probed_max_output: 0, probed_at: '' },
      },
    })
    await ctx.runProbe(m, 'reasoning')
    expect(modelProbeApi.probeReasoning).toHaveBeenCalledWith(7)
    expect(m.supports_thinking).toBe(1)
    expect(m.thinking_levels).toEqual(['low', 'medium'])
    expect(ElMessage.success).toHaveBeenCalledWith('支持思考：low/medium')
    expect(emit).toHaveBeenCalledWith('refresh')
  })

  it('上下文探测成功：提示「上下文有效 N」', async () => {
    const { ctx } = mountDrawer()
    const m = { id: 8, model: 'qwen' }
    modelProbeApi.probeContext.mockResolvedValue({
      data: { model_id: 8, result: { effective_context: 131072, requests: 6 }, probe: { probed_context: 131072, probed_at: '' } },
    })
    await ctx.runProbe(m, 'context')
    expect(m.probed_context).toBe(131072)
    expect(ElMessage.success).toHaveBeenCalledWith('上下文有效 131072')
  })

  it('探测 result.error：提示错误，徽章仍按状态刷新', async () => {
    const { ctx } = mountDrawer()
    const m = { id: 9, model: 'x', probed_context: 0 }
    modelProbeApi.probeContext.mockResolvedValue({
      data: { model_id: 9, result: { error: '厂商不可达或全部请求失败，未更新探测结论' }, probe: { probed_context: 0, probed_at: '' } },
    })
    await ctx.runProbe(m, 'context')
    expect(ElMessage.error).toHaveBeenCalledWith('厂商不可达或全部请求失败，未更新探测结论')
    expect(m.probed_context).toBe(0)
  })

  it('探测 result.skipped：warning 提示，不当成失败', async () => {
    const { ctx } = mountDrawer()
    const m = { id: 13, model: 'glm-5.2' }
    modelProbeApi.probeReasoning.mockResolvedValue({
      data: { model_id: 13, result: { skipped: 'anthropic 协议暂不支持自动探测' }, probe: { supports_thinking: -1, probed_at: '' } },
    })
    await ctx.runProbe(m, 'reasoning')
    expect(ElMessage.warning).toHaveBeenCalledWith('anthropic 协议暂不支持自动探测')
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('HTTP 失败：交由拦截器提示，组件不重复弹错', async () => {
    const { ctx } = mountDrawer()
    const m = { id: 10, model: 'x' }
    modelProbeApi.probeOutput.mockRejectedValue(new Error('boom'))
    await ctx.runProbe(m, 'output')
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ctx.probingId.value).toBeNull()
  })

  it('全量探测：先确认，取消则不发起请求', async () => {
    const { ctx } = mountDrawer()
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')
    await ctx.runProbeAll({ id: 11, model: 'x' })
    expect(modelProbeApi.probeAll).not.toHaveBeenCalled()
  })

  it('全量探测成功：三个维度徽章一并更新并汇总提示', async () => {
    const { ctx } = mountDrawer()
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    modelProbeApi.probeAll.mockResolvedValue({
      data: {
        model_id: 12,
        result: {
          reasoning: { thinking_levels: ['low'] },
          context: { effective_context: 131072 },
          output: { effective_max_output: 32768 },
          probed_at: '2026-10-12T09:30:00',
        },
        probe: {
          supports_thinking: 1,
          thinking_levels: ['low'],
          probed_context: 131072,
          probed_max_output: 32768,
          probed_at: '2026-10-12T09:30:00',
        },
      },
    })
    const m = { id: 12, model: 'x' }
    await ctx.runProbeAll(m)
    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(m.supports_thinking).toBe(1)
    expect(m.probed_context).toBe(131072)
    expect(m.probed_max_output).toBe(32768)
    expect(m.probed_at).toBe('2026-10-12T09:30:00')
    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('探测完成'))
    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('ctx 131072'))
  })

  it('批量探测：只确认一次，依次对全部启用中的模型发起全量探测', async () => {
    const { ctx, emit } = mountDrawer()
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    const m1 = { id: 21, model: 'mma', enabled: true }
    const m2 = { id: 22, model: 'mmb', enabled: true }
    modelProbeApi.probeAll.mockResolvedValue({
      data: {
        model_id: 0,
        result: { probed_at: '' },
        probe: { supports_thinking: 0, probed_context: 1000, probed_max_output: 2000, probed_at: '2026-10-13T10:00:00' },
      },
    })
    await ctx.runProbeAllModels({
      id: 5,
      name: '测试供应商',
      models: [m1, m2, { id: 23, model: 'mmc', enabled: false }],
    })
    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(modelProbeApi.probeAll).toHaveBeenCalledTimes(2)  // 停用模型跳过
    expect(modelProbeApi.probeAll).toHaveBeenCalledWith(21)
    expect(modelProbeApi.probeAll).toHaveBeenCalledWith(22)
    expect(m1.probed_context).toBe(1000)
    expect(m2.probed_context).toBe(1000)
    expect(ElMessage.success).toHaveBeenCalledWith('批量探测完成：2 个模型均已更新')
    expect(emit).toHaveBeenCalledWith('refresh')
  })

  it('批量探测：取消不发起请求；单个失败不中断其余', async () => {
    const { ctx } = mountDrawer()
    ElMessageBox.confirm.mockRejectedValueOnce('cancel')
    await ctx.runProbeAllModels({ id: 5, name: 'p', models: [{ id: 21, model: 'mma' }] })
    expect(modelProbeApi.probeAll).not.toHaveBeenCalled()

    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    modelProbeApi.probeAll
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ data: { model_id: 22, result: {}, probe: { supports_thinking: 0, probed_at: '' } } })
    await ctx.runProbeAllModels({ id: 5, name: 'p', models: [{ id: 21, model: 'mma' }, { id: 22, model: 'mmb' }] })
    expect(modelProbeApi.probeAll).toHaveBeenCalledTimes(2)
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('失败 1 个'))
  })

  it('批量探测：无启用中模型时提示且不请求', async () => {
    const { ctx } = mountDrawer()
    await ctx.runProbeAllModels({ id: 5, name: 'p', models: [] })
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('没有启用中的模型'))
    expect(modelProbeApi.probeAll).not.toHaveBeenCalled()
  })
})

describe('模型表单与预设填充（组件 setup 级）', () => {
  it('编辑模型回填：temperature / 备注 / 思考字段 / 视觉开关', () => {
    const { ctx } = mountDrawer()
    ctx.openModelForm(
      { id: 3 },
      {
        id: 7,
        model: 'deepseek-reasoner',
        context_size: 64000,
        max_tokens: 4096,
        supports_vision: true,
        enabled: true,
        temperature: 0.7,
        note: '推理用',
        supports_thinking: 1,
        thinking_levels: '["low","high"]',
      },
    )
    expect(ctx.modelDialog).toMatchObject({
      id: 7,
      model: 'deepseek-reasoner',
      context_size: 64000,
      temperature: 0.7,
      note: '推理用',
      supports_thinking: 1,
      thinking_levels: ['low', 'high'],
      supports_vision: true,
    })
  })

  it('视觉开关回填：supports_vision 缺省时回退旧的 multimodal 字段', () => {
    const { ctx } = mountDrawer()
    ctx.openModelForm({ id: 3 }, { id: 8, model: 'm', multimodal: true })
    expect(ctx.modelDialog.supports_vision).toBe(true)
    ctx.openModelForm({ id: 3 }, { id: 9, model: 'm', multimodal: true, supports_vision: false })
    expect(ctx.modelDialog.supports_vision).toBe(false)
    ctx.openModelForm({ id: 3 }, { id: 10, model: 'm' })
    expect(ctx.modelDialog.supports_vision).toBe(false)
  })

  it('新增模型默认：temperature 空、思考自动、级别空、视觉关', () => {
    const { ctx } = mountDrawer()
    ctx.openModelForm({ id: 3 })
    expect(ctx.modelDialog).toMatchObject({ id: null, temperature: null, note: '', supports_thinking: -1, thinking_levels: [], supports_vision: false })
  })

  it('保存模型透传新字段（temperature 留空传空串 = 跟随）', async () => {
    const { ctx, emit } = mountDrawer()
    ctx.openModelForm({ id: 3 }, { id: 7, model: 'm', context_size: 64000, max_tokens: 4096, temperature: '', note: '', supports_thinking: -1, thinking_levels: '' })
    ctx.modelDialog.note = '备注'
    ctx.modelDialog.supports_thinking = 1
    ctx.modelDialog.thinking_levels = ['low', 'high']
    await ctx.saveModel()
    expect(assistantApi.providers.saveModel).toHaveBeenCalledWith(
      3,
      expect.objectContaining({
        model_id: 7,
        model: 'm',
        temperature: '',
        note: '备注',
        supports_thinking: 1,
        thinking_levels: ['low', 'high'],
        supports_vision: false,
      }),
    )
    expect(emit).toHaveBeenCalledWith('refresh')

    ctx.modelDialog.temperature = 0.7
    await ctx.saveModel()
    expect(assistantApi.providers.saveModel).toHaveBeenLastCalledWith(3, expect.objectContaining({ temperature: 0.7 }))

    // 思考支持回到「自动」时级别传空数组（禁用态的值不透传）
    ctx.modelDialog.supports_thinking = -1
    await ctx.saveModel()
    expect(assistantApi.providers.saveModel).toHaveBeenLastCalledWith(3, expect.objectContaining({ supports_thinking: -1, thinking_levels: [] }))
  })

  it('保存模型透传视觉开关 supports_vision（旧 multimodal 字段同步保持一致）', async () => {
    const { ctx } = mountDrawer()
    ctx.openModelForm({ id: 3 }, { id: 7, model: 'm', supports_vision: false })
    ctx.modelDialog.supports_vision = true
    await ctx.saveModel()
    expect(assistantApi.providers.saveModel).toHaveBeenLastCalledWith(
      3,
      expect.objectContaining({ supports_vision: true, multimodal: true }),
    )

    ctx.modelDialog.supports_vision = false
    await ctx.saveModel()
    expect(assistantApi.providers.saveModel).toHaveBeenLastCalledWith(
      3,
      expect.objectContaining({ supports_vision: false, multimodal: false }),
    )
  })

  it('从预设填充：base_url + 候选模型，保存后自动添加', async () => {
    const { ctx } = mountDrawer()
    providerPresetsApi.list.mockResolvedValue({
      data: {
        presets: [
          {
            name: 'deepseek',
            label: 'DeepSeek',
            protocol: 'openai',
            base_url: 'https://api.deepseek.com/v1',
            models: ['deepseek-chat', 'deepseek-reasoner'],
            note: '官方接口',
            docs_url: 'https://platform.deepseek.com',
          },
        ],
        count: 1,
      },
    })
    await ctx.loadPresets()
    ctx.openProviderForm()
    ctx.applyPresetFill('deepseek')
    expect(ctx.provDialog.base_url).toBe('https://api.deepseek.com/v1')
    expect(ctx.provDialog.protocol).toBe('openai')
    expect(ctx.provDialog.name).toBe('DeepSeek')
    expect(ctx.provDialog.presetModels).toEqual(['deepseek-chat', 'deepseek-reasoner'])
    expect(ctx.presetFillInfo.value).toMatchObject({ note: '官方接口', docs_url: 'https://platform.deepseek.com' })

    assistantApi.providers.save.mockResolvedValue({ data: { provider: { id: 42 } } })
    await ctx.saveProvider()
    expect(assistantApi.providers.saveModel).toHaveBeenCalledTimes(2)
    expect(assistantApi.providers.saveModel).toHaveBeenCalledWith(42, expect.objectContaining({ model: 'deepseek-chat' }))
    expect(assistantApi.providers.saveModel).toHaveBeenCalledWith(42, expect.objectContaining({ model: 'deepseek-reasoner' }))
  })

  it('清空预设选择：候选模型与提示一并清掉', () => {
    const { ctx } = mountDrawer()
    ctx.openProviderForm()
    ctx.applyPresetFill('not-exist')
    expect(ctx.provDialog.presetModels).toEqual([])
    expect(ctx.presetFillInfo.value).toBeNull()
  })
})
