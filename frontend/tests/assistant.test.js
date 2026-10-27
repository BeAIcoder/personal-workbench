import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { readFileSync } from 'node:fs'
import ChatPanel from '../src/components/assistant/ChatPanel.vue'
import { CANONICAL_LEVELS } from '../src/utils/modelCapabilities'
import {
  ASSISTANT_ACCEPT,
  IMAGE_EXTS,
  attachmentImageSrc,
  isImageAttachment,
  isImageFile,
  sessionExportUrl,
  sessionListParams,
} from '../src/utils/assistant'

// node 环境无 DOM：mock 掉消息组件，直接驱动编译后的 setup()
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    // 直接驱动 setup() 时无组件实例，生命周期注册会告警；测试里禁音即可
    onBeforeUnmount: () => {},
  }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    assistantApi: {
      ...actual.assistantApi,
      config: { ...actual.assistantApi.config, saveGlobals: vi.fn(), test: vi.fn() },
      history: vi.fn(),
      providers: { ...actual.assistantApi.providers, activate: vi.fn() },
    },
  }
})

const { assistantApi } = await import('../src/api')

function mountPanel(overrides = {}) {
  const props = reactive({
    sessionId: '',
    agents: [],
    selectedAgent: null,
    llmInfo: {},
    models: [],
    workModes: {},
    reasoning: 'default',
    teamConfig: {},
    ...overrides,
  })
  const emit = vi.fn()
  const ctx = ChatPanel.setup(props, { expose: () => {}, emit })
  return { ctx, props, emit }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('会话列表归档过滤参数', () => {
  it('进行中 → archived=false，已归档 → archived=true', () => {
    expect(sessionListParams(false)).toEqual({ archived: 'false' })
    expect(sessionListParams(true)).toEqual({ archived: 'true' })
  })

  it('缺省按进行中处理', () => {
    expect(sessionListParams()).toEqual({ archived: 'false' })
    expect(sessionListParams(undefined)).toEqual({ archived: 'false' })
  })
})

describe('会话导出 URL', () => {
  it('拼接 API_BASE 与导出路径', () => {
    expect(sessionExportUrl('s_abc123', '/api')).toBe('/api/assistant/sessions/s_abc123/export')
  })

  it('baseURL 尾部斜杠不重复；缺省 /api', () => {
    expect(sessionExportUrl('s_1', '/api/')).toBe('/api/assistant/sessions/s_1/export')
    expect(sessionExportUrl('s_1')).toBe('/api/assistant/sessions/s_1/export')
    expect(sessionExportUrl('s_1', 'http://127.0.0.1:8000/api')).toBe('http://127.0.0.1:8000/api/assistant/sessions/s_1/export')
  })

  it('session id 特殊字符转义', () => {
    expect(sessionExportUrl('a b/c', '/api')).toBe('/api/assistant/sessions/a%20b%2Fc/export')
  })
})

describe('图片类型判断', () => {
  it('后缀命中（大小写不敏感）', () => {
    expect(isImageFile({ name: 'a.png' })).toBe(true)
    expect(isImageFile({ name: 'a.JPG' })).toBe(true)
    expect(isImageFile({ name: 'a.webp' })).toBe(true)
    expect(isImageFile({ name: 'a.gif' })).toBe(true)
    expect(isImageFile({ name: 'a.txt' })).toBe(false)
    expect(isImageFile({ name: 'noext' })).toBe(false)
  })

  it('MIME 命中', () => {
    expect(isImageFile({ name: 'blob', type: 'image/png' })).toBe(true)
    expect(isImageFile({ name: 'a.csv', type: 'text/csv' })).toBe(false)
  })

  it('空值安全', () => {
    expect(isImageFile(null)).toBe(false)
    expect(isImageFile(undefined)).toBe(false)
    expect(isImageFile({})).toBe(false)
  })

  it('上传响应 kind=image 即图片（后端契约）', () => {
    expect(isImageAttachment({ kind: 'image', name: 'x.bin' })).toBe(true)
    expect(isImageAttachment({ kind: 'file', name: 'x.png' })).toBe(true)
    expect(isImageAttachment({ kind: 'csv_converted', name: 'x.csv' })).toBe(false)
    expect(isImageAttachment({ kind: 'file', name: 'x.xlsx' })).toBe(false)
    expect(isImageAttachment(null)).toBe(false)
  })
})

describe('图片回显地址', () => {
  it('上传响应 url：绝对地址直接用，相对地址拼 API_BASE', () => {
    expect(attachmentImageSrc({ url: 'https://cdn.example.com/a.png' }, '/api')).toBe('https://cdn.example.com/a.png')
    expect(attachmentImageSrc({ url: '/assistant/uploads/s_1/a.png' }, '/api')).toBe('/api/assistant/uploads/s_1/a.png')
  })

  it('无 url 时用本地 objectURL 预览', () => {
    expect(attachmentImageSrc({ previewUrl: 'blob:http://x/abc' }, '/api')).toBe('blob:http://x/abc')
  })

  it('url 优先于本地预览', () => {
    expect(attachmentImageSrc({ url: '/a.png', previewUrl: 'blob:xxx' }, '/api')).toBe('/api/a.png')
  })

  it('后端只给服务器文件系统 path（Windows 盘符）时无浏览器可访问地址', () => {
    expect(attachmentImageSrc({ path: 'D:\\data\\uploads\\s_1\\a.png' }, '/api')).toBe('')
    expect(attachmentImageSrc({ path: '/data/uploads/s_1/a.png' }, '/api')).toBe('/api/data/uploads/s_1/a.png')
    expect(attachmentImageSrc({}, '/api')).toBe('')
    expect(attachmentImageSrc(null, '/api')).toBe('')
  })
})

describe('附件 accept 清单', () => {
  it('保留原有类型并包含图片与 PDF/Word/PPT', () => {
    for (const ext of ['.txt', '.md', '.csv', '.json', '.py', '.sql', '.xlsx', '.xls']) {
      expect(ASSISTANT_ACCEPT).toContain(ext)
    }
    for (const ext of IMAGE_EXTS) {
      expect(ASSISTANT_ACCEPT).toContain('.' + ext)
    }
    for (const ext of ['.pdf', '.doc', '.docx', '.ppt', '.pptx']) {
      expect(ASSISTANT_ACCEPT).toContain(ext)
    }
    expect(IMAGE_EXTS).toEqual(['png', 'jpg', 'jpeg', 'webp', 'gif'])
  })
})

// ---------------- 动态思考下拉（Task 6A） ----------------
describe('聊天区动态思考下拉（由激活模型生成）', () => {
  it('强制思考模型（GLM-5.3）：档位 low/high/max，无「关闭」', () => {
    const { ctx } = mountPanel({
      teamConfig: { model: 'glm-5.3', thinking_levels: ['max', 'high', 'low'], thinking_can_disable: false, default_thinking_level: 'high' },
    })
    const opts = ctx.reasoningOpts.value
    expect(opts.options.map((o) => o.value)).toEqual(['default', 'low', 'high', 'max'])
    expect(opts.options.some((o) => o.value === 'none')).toBe(false)
    expect(opts.disabled).toBe(false)
  })

  it('未知模型（无 teamConfig 能力字段）：canonical 七档全量且标注未确认', () => {
    const { ctx } = mountPanel()
    const opts = ctx.reasoningOpts.value
    expect(opts.unconfirmed).toBe(true)
    expect(opts.levels).toEqual(CANONICAL_LEVELS)
    expect(opts.options.map((o) => o.value)).toEqual(['default', ...CANONICAL_LEVELS])
  })

  it('明确不支持思考的模型：下拉禁用', () => {
    const { ctx } = mountPanel({ teamConfig: { model: 'm', supports_thinking: 0 } })
    expect(ctx.reasoningOpts.value.disabled).toBe(true)
    expect(ctx.reasoningOpts.value.options.map((o) => o.value)).toEqual(['default'])
  })

  it('切换模型后已存档档位不在目标声明集 → 改「跟随模型」并保存', async () => {
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: '' } } })
    const { ctx, props } = mountPanel({
      reasoning: 'medium',
      teamConfig: { model: 'm1', thinking_levels: ['low', 'medium', 'high'], thinking_can_disable: true },
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('medium')  // 挂载时合法：不触发归一
    expect(assistantApi.config.saveGlobals).not.toHaveBeenCalled()
    props.teamConfig = { model: 'm2', thinking_levels: ['low', 'high'], thinking_can_disable: false }
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))  // 等 saveReasoning 的 await 链走完
    expect(ctx.reasoningEffort.value).toBe('default')
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: '' })
    expect(ElMessage.success).toHaveBeenCalledWith('思考深度已恢复为跟随模型')
  })

  it('切换模型后合法档位保持不变、不重复保存', async () => {
    const { ctx, props } = mountPanel({
      reasoning: 'high',
      teamConfig: { model: 'm1', thinking_levels: ['low', 'high'], thinking_can_disable: true },
    })
    props.teamConfig = { model: 'm2', thinking_levels: ['max', 'high', 'low'], thinking_can_disable: false }
    await nextTick()
    expect(ctx.reasoningEffort.value).toBe('high')
    expect(assistantApi.config.saveGlobals).not.toHaveBeenCalled()
  })

  it('canonical 七档都能原样提交（default 存空串 = 跟随模型）', async () => {
    const { ctx } = mountPanel()
    for (const lv of CANONICAL_LEVELS) {
      assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: lv } } })
      ctx.reasoningEffort.value = lv
      await ctx.saveReasoning()
      expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: lv })
    }
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: '' } } })
    ctx.reasoningEffort.value = 'default'
    await ctx.saveReasoning()
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: '' })
  })

  it('提示按各 Agent 生效模型自适应：绑定模型显示默认档与强制思考', () => {
    const { ctx } = mountPanel({
      agents: [{ name: 'ledger', label: '账务专家', model_id: 7 }],
      selectedAgent: 'ledger',
      models: [
        { id: 7, label: '智谱 · glm-5.3', model: 'glm-5.3', default_thinking_level: 'high', thinking_can_disable: false, capability: { reasoning_style: 'effort' } },
      ],
    })
    const hint = ctx.agentModelHint.value
    expect(hint).toContain('账务专家')
    expect(hint).toContain('glm-5.3')
    expect(hint).toContain('默认档 high')
    expect(hint).toContain('强制思考')
    expect(hint).toContain('effort')
  })

  it('未绑定模型的专家 / 智能路由：提示跟随激活模型', () => {
    const unbound = mountPanel({ agents: [{ name: 'a', label: '助手A' }], selectedAgent: 'a', llmInfo: { model: 'kimi-k3' } })
    expect(unbound.ctx.agentModelHint.value).toContain('未绑定模型')
    const routed = mountPanel({ llmInfo: { model: 'kimi-k3' }, teamConfig: { model: 'kimi-k3', thinking_levels: ['low', 'high', 'max'] } })
    expect(routed.ctx.agentModelHint.value).toContain('kimi-k3')
    expect(routed.ctx.agentModelHint.value).toContain('low/high/max')
  })

  it('旧 off 在可关闭模型上迁移为 canonical none 并保存（偏好不被改回开启）', async () => {
    const { ctx, props } = mountPanel({
      reasoning: 'low',
      teamConfig: { model: 'kimi-k3', thinking_levels: ['none', 'low', 'high', 'max'], thinking_can_disable: true },
    })
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: 'none' } } })
    props.reasoning = 'off'
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('none')
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: 'none' })
    expect(ElMessage.success).toHaveBeenCalledWith('思考深度已生效')
  })

  it('旧 off 在强制思考模型上才回「跟随模型」并保存', async () => {
    const { ctx, props } = mountPanel({
      reasoning: 'low',
      teamConfig: { model: 'glm-5.3', thinking_levels: ['max', 'high', 'low'], thinking_can_disable: false },
    })
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: '' } } })
    props.reasoning = 'off'
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('default')
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: '' })
  })

  it('切换模型后旧 off 按目标模型迁移：可关闭 → none，强制思考 → 跟随模型', async () => {
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: 'none' } } })
    const { ctx, props } = mountPanel({
      reasoning: 'off',
      teamConfig: { model: 'm1', thinking_levels: ['low', 'high'], thinking_can_disable: true },
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('none')  // 挂载即归一（keep-alive 重挂场景）
    props.teamConfig = { model: 'm2', thinking_levels: ['low', 'high'], thinking_can_disable: true }
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('none')
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: 'none' })

    // 再切到强制思考模型：none 不可兑现 → 回「跟随模型」
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: '' } } })
    props.teamConfig = { model: 'm3', thinking_levels: ['max', 'high', 'low'], thinking_can_disable: false }
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('default')
    expect(assistantApi.config.saveGlobals).toHaveBeenLastCalledWith({ reasoning_effort: '' })
  })

  it('旧 off 迁移为 none 后不被声明集门控打回（声明集无 none 但可关闭）', async () => {
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: 'none' } } })
    const { ctx, props } = mountPanel({
      reasoning: 'off',
      teamConfig: { model: 'm1', thinking_levels: ['low', 'medium', 'high'], reasoning_style: 'budget', thinking_can_disable: true },
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('none')
    props.teamConfig = { model: 'qwen', thinking_levels: ['low', 'medium', 'high'], reasoning_style: 'budget', thinking_can_disable: true }
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('none')
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: 'none' })
    // 父组件回填 canonical none：不被声明集门控打回、不重复保存
    const calls = assistantApi.config.saveGlobals.mock.calls.length
    props.reasoning = 'none'
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('none')
    expect(assistantApi.config.saveGlobals.mock.calls.length).toBe(calls)
  })

  it('挂载时即归一：keep-alive 重挂后旧 off 迁移为 none（可关闭模型）', async () => {
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: 'none' } } })
    const { ctx } = mountPanel({
      reasoning: 'off',
      teamConfig: { model: 'kimi-k3', thinking_levels: ['none', 'low', 'high'], thinking_can_disable: true },
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('none')
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: 'none' })
  })

  it('挂载时即归一：强制思考模型上的旧 off 回「跟随模型」', async () => {
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: '' } } })
    const { ctx } = mountPanel({
      reasoning: 'off',
      teamConfig: { model: 'glm-5.3', thinking_levels: ['max', 'high', 'low'], thinking_can_disable: false },
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('default')
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: '' })
  })

  it('非法存档值（非 off）仍回「跟随模型」并保存', async () => {
    const { ctx, props } = mountPanel({ reasoning: 'low', teamConfig: { model: 'm1', thinking_levels: ['low'] } })
    assistantApi.config.saveGlobals.mockResolvedValue({ data: { config: { reasoning_effort: '' } } })
    props.reasoning = 'bogus'
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))
    expect(ctx.reasoningEffort.value).toBe('default')
    expect(assistantApi.config.saveGlobals).toHaveBeenCalledWith({ reasoning_effort: '' })
  })

  it('下拉选项由模型生成而非硬编码（含禁用态接线）', () => {
    const src = readFileSync(new URL('../src/components/assistant/ChatPanel.vue', import.meta.url), 'utf8')
    expect(src).toMatch(/v-for="o in reasoningOpts\.options"/)
    expect(src).toMatch(/:disabled="reasoningOpts\.disabled"/)
    expect(src).not.toMatch(/value="off"/)  // 旧硬编码 off 已由 canonical none 取代
  })
})
