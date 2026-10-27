// Task 6A：设置页同步、能力展示与动态思考下拉的前端测试（严格 TDD：先 RED 后 GREEN）。
// 覆盖：
//  - utils/modelCapabilities.js 纯函数（三态徽章 / 档位选项 / 同步摘要 / 多模态）
//  - api 层同步端点契约（URL 与请求体）
//  - SettingsDrawer setup 级交互（同步按钮、忙态锁、partial/truncated、预设与管控凭证、
//    每模型实测传 model_id、恢复同步值、模型列表搜索与分批）
//  - 模板接线（aria-live / content-visibility）静态断言（无 DOM 环境，沿用项目既有范式）
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessage, ElMessageBox } from 'element-plus'
import { readFileSync } from 'node:fs'
import {
  CANONICAL_LEVELS,
  capabilityBadges,
  formatK,
  isMultimodal,
  levelAllowed,
  parseLevels,
  probeAtShort,
  reasoningOptions,
  syncSummary,
  visionState,
} from '../src/utils/modelCapabilities'

// node 环境无 DOM：mock 掉消息组件，直接驱动编译后的 setup()
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useSSRContext: () => ({ modules: new Set() }) }
})

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

const syncInst = { post: vi.fn(), get: vi.fn(), interceptors: { response: { use: vi.fn() } } }
vi.mock('axios', () => ({
  default: { create: () => syncInst, post: vi.fn() },
  create: () => syncInst,
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
    providerPresetsApi: { list: vi.fn(), reload: vi.fn() },
    assistantApi: {
      ...actual.assistantApi,
      config: { ...actual.assistantApi.config, test: vi.fn(), saveGlobals: vi.fn() },
      providers: {
        ...actual.assistantApi.providers,
        save: vi.fn(),
        saveModel: vi.fn(),
        syncModels: vi.fn(),
      },
      models: { ...actual.assistantApi.models, resetOverrides: vi.fn() },
    },
  }
})

const { assistantApi } = await import('../src/api')
const SettingsDrawer = (await import('../src/components/assistant/SettingsDrawer.vue')).default

function mountDrawer(providers = []) {
  const emit = vi.fn()
  const ctx = SettingsDrawer.setup(
    { visible: false, providers, presets: [], teamConfig: {}, initialTab: 'providers' },
    { expose: () => {}, emit },
  )
  return { ctx, emit }
}

function badgeOf(badges, key) {
  return badges.find((b) => b.key === key)
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------- 纯函数：能力徽章 ----------------
describe('能力徽章三态（支持/不支持/未知）', () => {
  it('思考：支持 ✓、不支持 ✗、未知「未确认」且不显示红叉', () => {
    const sup = badgeOf(capabilityBadges({ supports_thinking: 1 }), 'thinking')
    expect(sup).toMatchObject({ text: '思考✓', type: 'success', dashed: false })
    const no = badgeOf(capabilityBadges({ supports_thinking: 0 }), 'thinking')
    expect(no).toMatchObject({ text: '思考✗', type: 'info', dashed: false })
    const un = badgeOf(capabilityBadges({}), 'thinking')
    expect(un).toMatchObject({ text: '思考未确认', type: 'info', dashed: true })
    expect(un.text).not.toContain('✗')
  })

  it('工具：supports_tools 三态，未知为虚线「未确认」', () => {
    expect(badgeOf(capabilityBadges({ supports_tools: 1 }), 'tools')).toMatchObject({ text: '工具✓', type: 'success', dashed: false })
    expect(badgeOf(capabilityBadges({ supports_tools: 0 }), 'tools')).toMatchObject({ text: '工具✗', type: 'info', dashed: false })
    expect(badgeOf(capabilityBadges({}), 'tools')).toMatchObject({ text: '工具未确认', type: 'info', dashed: true })
  })

  it('ctx/input/out：分别取实测上下文、最大输入、最大输出', () => {
    const badges = capabilityBadges({ probed_context: 131072, max_input_tokens: 200000, probed_max_output: 32768 })
    expect(badgeOf(badges, 'ctx')).toMatchObject({ text: 'ctx 128k' })
    expect(badgeOf(badges, 'input')).toMatchObject({ text: '输入 195k' })
    expect(badgeOf(badges, 'out')).toMatchObject({ text: 'out 32k' })
  })

  it('ctx/out 缺实测时回退声明值，且标题注明来源', () => {
    const badges = capabilityBadges({ context_size: 64000, max_tokens: 4096 })
    expect(badgeOf(badges, 'ctx')).toMatchObject({ text: 'ctx 63k' })
    expect(badgeOf(badges, 'ctx').title).toContain('声明')
    expect(badgeOf(badges, 'out')).toMatchObject({ text: 'out 4k' })
    expect(badgeOf(capabilityBadges({}), 'ctx')).toBeUndefined()
    expect(badgeOf(capabilityBadges({}), 'out')).toBeUndefined()
  })

  it('视觉与多模态区分：输入含图片即多模态；视觉只看 supports_vision', () => {
    const both = capabilityBadges({ input_modalities: ['text', 'image'], supports_vision: 1 })
    expect(badgeOf(both, 'vision')).toMatchObject({ text: '视觉✓', type: 'success' })
    expect(badgeOf(both, 'multimodal')).toBeDefined()
    expect(isMultimodal({ input_modalities: ['text', 'image'] })).toBe(true)

    // 输入模态含图片但 supports_vision=0：仍算多模态，视觉显示 ✗（两者不可互相代替）
    const onlyMm = capabilityBadges({ input_modalities: ['text', 'image'], supports_vision: 0 })
    expect(badgeOf(onlyMm, 'vision')).toMatchObject({ text: '视觉✗', type: 'info' })
    expect(badgeOf(onlyMm, 'multimodal')).toBeDefined()

    // 纯文本：既不多模态也不视觉
    const textOnly = capabilityBadges({ input_modalities: ['text'], supports_vision: 0 })
    expect(badgeOf(textOnly, 'multimodal')).toBeUndefined()

    // 视觉未知（缺省）时不显示视觉徽章，不猜
    expect(badgeOf(capabilityBadges({ input_modalities: ['text'] }), 'vision')).toBeUndefined()
    expect(visionState({ multimodal: true })).toBe('supported')  // 旧字段回退
    expect(visionState({ supports_vision: false, multimodal: true })).toBe('unsupported')
    expect(visionState({})).toBe('unknown')
  })

  it('来源文案：手动覆盖 / 实测 / 同步', () => {
    const manual = badgeOf(capabilityBadges({ capability: { sources: { supports_tools: 'manual' }, manual_fields: ['supports_tools'] } }), 'source')
    expect(manual.text).toBe('来源：手动')
    expect(manual.title).toContain('supports_tools')
    const probe = badgeOf(capabilityBadges({ capability: { sources: { supports_thinking: 'probe' } } }), 'source')
    expect(probe.text).toBe('来源：实测')
    const synced = badgeOf(capabilityBadges({ capability: { sources: { supports_tools: 'provider_api' } } }), 'source')
    expect(synced.text).toBe('来源：同步')
    const catalog = badgeOf(capabilityBadges({ capability: { sources: { supports_tools: 'official_catalog' } } }), 'source')
    expect(catalog.text).toBe('来源：同步')
    expect(badgeOf(capabilityBadges({}), 'source')).toBeUndefined()
  })

  it('同步时间徽章：只保留到分钟', () => {
    expect(badgeOf(capabilityBadges({ synced_at: '2026-10-26T09:30:45' }), 'synced')).toMatchObject({ text: '同步于 2026-10-26 09:30' })
    expect(badgeOf(capabilityBadges({}), 'synced')).toBeUndefined()
  })

  it('思考支持时附带档位徽章；显式未知/不支持时不带', () => {
    expect(badgeOf(capabilityBadges({ supports_thinking: 1, thinking_levels: ['low', 'high', 'max'] }), 'levels')).toMatchObject({ text: 'low/high/max' })
    expect(badgeOf(capabilityBadges({ supports_thinking: 0, thinking_levels: ['low'] }), 'levels')).toBeUndefined()
    expect(badgeOf(capabilityBadges({ supports_thinking: -1 }), 'levels')).toBeUndefined()
  })

  it('空模型不抛错', () => {
    expect(() => capabilityBadges(null)).not.toThrow()
    expect(capabilityBadges(null).length).toBeGreaterThan(0)
  })
})

// ---------------- 纯函数：思考档位选项 ----------------
describe('思考档位选项（ChatPanel 动态下拉同源）', () => {
  it('canonical 七档齐全', () => {
    expect(CANONICAL_LEVELS).toEqual(['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'])
  })

  it('Kimi：low/high/max 三档，强制思考不出现「关闭」', () => {
    const o = reasoningOptions({ supports_thinking: 1, thinking_levels: ['low', 'high', 'max'], thinking_can_disable: false, reasoning_style: 'effort' })
    expect(o.support).toBe('supported')
    expect(o.levels).toEqual(['low', 'high', 'max'])
    expect(o.options.map((x) => x.value)).toEqual(['default', 'low', 'high', 'max'])
    expect(o.options.some((x) => x.value === 'none')).toBe(false)
    expect(o.disabled).toBe(false)
    expect(o.canDisable).toBe(false)
  })

  it('Step：low/medium/high 三档', () => {
    const o = reasoningOptions({ thinking_levels: ['low', 'medium', 'high'], thinking_can_disable: false })
    expect(o.levels).toEqual(['low', 'medium', 'high'])
    expect(o.options.map((x) => x.value)).toEqual(['default', 'low', 'medium', 'high'])
  })

  it('GLM 强制思考：声明档之外的 canonical 档不外露', () => {
    const o = reasoningOptions({ thinking_levels: ['max', 'high', 'low'], thinking_can_disable: false })
    expect(o.levels).toEqual(['low', 'high', 'max'])
    expect(levelAllowed({ thinking_levels: ['max', 'high', 'low'], thinking_can_disable: false }, 'xhigh')).toBe(false)
    expect(levelAllowed({ thinking_levels: ['max', 'high', 'low'], thinking_can_disable: false }, 'none')).toBe(false)
  })

  it('SiliconFlow budget 风格：style=budget，选项按声明档 + 可关闭时提供「关闭」', () => {
    const o = reasoningOptions({ thinking_levels: ['low', 'medium', 'high'], reasoning_style: 'budget', thinking_can_disable: true })
    expect(o.style).toBe('budget')
    expect(o.levels).toEqual(['low', 'medium', 'high'])
    // none 只受 can_disable 约束（与后端同口径），声明集没有 none 也提供
    expect(o.canClose).toBe(true)
    expect(o.options.map((x) => x.value)).toEqual(['default', 'none', 'low', 'medium', 'high'])
  })

  it('强制思考模型不提供「关闭」；levelAllowed 对 none 同样只受 can_disable 约束', () => {
    const forced = { thinking_levels: ['low', 'high'], thinking_can_disable: false }
    expect(reasoningOptions(forced).canClose).toBe(false)
    expect(reasoningOptions(forced).options.some((x) => x.value === 'none')).toBe(false)
    expect(levelAllowed(forced, 'none')).toBe(false)
    const closable = { thinking_levels: ['low', 'high'], thinking_can_disable: true }
    expect(levelAllowed(closable, 'none')).toBe(true)
    expect(levelAllowed({}, 'none')).toBe(true)  // 未知：兼容
  })

  it('可关闭且声明 none：出现「关闭」项', () => {
    const o = reasoningOptions({ thinking_levels: ['none', 'low', 'high', 'max'], thinking_can_disable: true })
    expect(o.levels).toEqual(['none', 'low', 'high', 'max'])
    expect(o.options[1]).toMatchObject({ value: 'none', label: '🧠 思考：关闭' })
  })

  it('未知模型：保留兼容全量档位并标注未确认', () => {
    const o = reasoningOptions({})
    expect(o.support).toBe('unknown')
    expect(o.declared).toBe(false)
    expect(o.unconfirmed).toBe(true)
    expect(o.levels).toEqual(['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'])
    expect(o.options.map((x) => x.value)).toEqual(['default', ...CANONICAL_LEVELS])
    expect(o.disabled).toBe(false)
    // 未知时任何 canonical 档都允许（兼容），由后端自行忽略
    expect(levelAllowed({}, 'xhigh')).toBe(true)
  })

  it('明确不支持思考：下拉禁用且只有「跟随模型」', () => {
    const o = reasoningOptions({ supports_thinking: 0 })
    expect(o.support).toBe('unsupported')
    expect(o.levels).toEqual([])
    expect(o.options.map((x) => x.value)).toEqual(['default'])
    expect(o.disabled).toBe(true)
    expect(o.note).toContain('不支持')
  })

  it('声明档位即视为支持思考（supports_thinking 缺省不误判为未知）', () => {
    expect(reasoningOptions({ thinking_levels: ['low'] }).support).toBe('supported')
  })

  it('声明档里的非法值被剔除，不外露', () => {
    const o = reasoningOptions({ thinking_levels: ['low', 'bogus', 'high'] })
    expect(o.levels).toEqual(['low', 'high'])
  })

  it('跟随模型项带默认档提示（已知时）', () => {
    const o = reasoningOptions({ thinking_levels: ['low', 'high'], default_thinking_level: 'high' })
    expect(o.options[0]).toMatchObject({ value: 'default' })
    expect(o.options[0].title).toContain('high')
  })
})

// ---------------- 纯函数：同步摘要 ----------------
describe('同步结果摘要', () => {
  it('在线完整成功：success 级并给出四项汇总', () => {
    const s = syncSummary({
      ok: true, partial: false, online: true, source: 'provider_api',
      added: 2, updated: 1, unchanged: 4, skipped: [{ model: 'old-x', reason: '已下线' }],
      warnings: [], truncated: false, errors: [],
    })
    expect(s).toMatchObject({ level: 'success', ok: true, online: true, partial: false, truncated: false, added: 2, updated: 1, unchanged: 4 })
    expect(s.text).toBe('同步完成：新增 2 · 更新 1 · 未变 4 · 跳过 1')
  })

  it('partial（在线不可达、目录兜底）：warning 级并明示非实时', () => {
    const s = syncSummary({
      ok: true, partial: true, online: false, source: 'official_catalog',
      added: 1, updated: 0, unchanged: 5, skipped: [],
      warnings: ['在线列表获取失败，本次仅按能力目录同步（非实时全量）'], truncated: false, errors: [],
    })
    expect(s.level).toBe('warning')
    expect(s.partial).toBe(true)
    expect(s.online).toBe(false)
    expect(s.text).toContain('能力目录')
    expect(s.text).toContain('新增 1')
  })

  it('truncated（分页上限）：warning 级并明示可能不完整', () => {
    const s = syncSummary({ ok: true, partial: false, online: true, added: 3, updated: 0, unchanged: 0, skipped: [], warnings: [], truncated: true, errors: [] })
    expect(s.level).toBe('warning')
    expect(s.truncated).toBe(true)
    expect(s.text).toContain('分页上限')
  })

  it('ok=false（整批回滚）：error 级并透出错误原因', () => {
    const s = syncSummary({ ok: false, partial: false, online: true, added: 0, updated: 0, unchanged: 0, skipped: [], warnings: [], truncated: false, errors: ['同步写入失败，已整批回滚（OperationalError）'] })
    expect(s.level).toBe('error')
    expect(s.ok).toBe(false)
    expect(s.text).toContain('同步未完成')
    expect(s.text).toContain('整批回滚')
  })

  it('ok=false 但无 errors：不给假成功也不空白', () => {
    const s = syncSummary({ ok: false, added: 0, updated: 0, unchanged: 0, skipped: [], warnings: [], truncated: false, errors: [] })
    expect(s.level).toBe('error')
    expect(s.text).toBe('同步未完成：后端未返回原因')
  })

  it('计数缺省按 0，不显示 NaN；无有效响应不假成功', () => {
    expect(syncSummary({ ok: true }).text).toBe('同步完成：新增 0 · 更新 0 · 未变 0 · 跳过 0')
    expect(syncSummary({}).level).toBe('error')
    expect(syncSummary(null).level).toBe('error')
    expect(syncSummary('x').level).toBe('error')
  })
})

// ---------------- 共享小函数（与 SettingsDrawer 同源，单一实现） ----------------
describe('k 格式化与档位解析', () => {
  it('formatK 按 1024 取整，超 1024k 转 m', () => {
    expect(formatK(131072)).toBe('128k')
    expect(formatK(1048576)).toBe('1m')
    expect(formatK(0)).toBe('')
    expect(formatK('abc')).toBe('')
  })

  it('parseLevels 兼容数组/JSON/逗号分隔', () => {
    expect(parseLevels('["low","high"]')).toEqual(['low', 'high'])
    expect(parseLevels('low, high')).toEqual(['low', 'high'])
    expect(parseLevels('not-json')).toEqual([])
    expect(parseLevels(null)).toEqual([])
  })

  it('probeAtShort 只保留到分钟', () => {
    expect(probeAtShort('2026-10-12T09:30:45')).toBe('2026-10-12 09:30')
    expect(probeAtShort('昨天')).toBe('昨天')
  })
})

// ---------------- API 契约 ----------------
// 顶层 import 拿到的是被 mock 的 assistantApi（供 setup 测试）；这里用 importActual
// 拿真实 api 模块（其 axios 依赖仍被 mock 成 spy），断言我们自己发出的 URL/请求体契约
describe('同步端点 API 契约', () => {
  it('syncModels POST 到 /providers/{id}/sync-models 并带同步选项', async () => {
    const { assistantApi: api } = await vi.importActual('../src/api')
    syncInst.post.mockResolvedValue({ data: { ok: true } })
    await api.providers.syncModels(7, { include_disabled: true, override_manual: true })
    expect(syncInst.post).toHaveBeenCalledWith(
      '/assistant/providers/7/sync-models',
      { include_disabled: true, override_manual: true },
      expect.objectContaining({ timeout: 180000 }),
    )
  })

  it('syncModels 缺省选项为 false（不同步下线、不覆盖手动）', async () => {
    const { assistantApi: api } = await vi.importActual('../src/api')
    syncInst.post.mockResolvedValue({ data: { ok: true } })
    await api.providers.syncModels(7)
    expect(syncInst.post).toHaveBeenCalledWith(
      '/assistant/providers/7/sync-models',
      { include_disabled: false, override_manual: false },
      expect.objectContaining({ timeout: 180000 }),
    )
  })

  it('resetOverrides POST 到 /models/{id}/capabilities/reset-overrides', async () => {
    const { assistantApi: api } = await vi.importActual('../src/api')
    syncInst.post.mockResolvedValue({ data: { ok: true } })
    await api.models.resetOverrides(9)
    expect(syncInst.post).toHaveBeenCalledWith('/assistant/models/9/capabilities/reset-overrides')
  })
})

// ---------------- 供应商同步交互 ----------------
const SYNC_OK = {
  ok: true, partial: false, provider_id: 3, source: 'provider_api', online: true,
  added: 2, updated: 1, unchanged: 4, skipped: [{ model: 'old-x', reason: '已下线' }],
  warnings: [], truncated: false, errors: [], synced_at: '2026-10-26T09:00:00',
  providers: [],
}

describe('供应商同步交互（setup 级）', () => {
  it('同步成功：汇总四项并刷新，结果区为 success 级', async () => {
    const { ctx, emit } = mountDrawer()
    assistantApi.providers.syncModels.mockResolvedValue({ data: SYNC_OK })
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(assistantApi.providers.syncModels).toHaveBeenCalledWith(3, { override_manual: false })
    expect(ElMessage.success).toHaveBeenCalledWith('同步完成：新增 2 · 更新 1 · 未变 4 · 跳过 1')
    expect(ctx.syncResults[3]).toMatchObject({ level: 'success', added: 2, updated: 1, unchanged: 4 })
    expect(emit).toHaveBeenCalledWith('refresh')
    expect(ctx.syncingProviderId.value).toBeNull()
  })

  it('partial：warning 提示明示目录兜底，仍刷新（模型确实已更新）', async () => {
    const { ctx, emit } = mountDrawer()
    assistantApi.providers.syncModels.mockResolvedValue({
      data: { ...SYNC_OK, partial: true, online: false, source: 'official_catalog', added: 1, updated: 0, unchanged: 5, skipped: [], warnings: ['在线列表获取失败，本次仅按能力目录同步（非实时全量）'] },
    })
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('能力目录'))
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ctx.syncResults[3].level).toBe('warning')
    expect(emit).toHaveBeenCalledWith('refresh')
  })

  it('truncated：warning 提示明示分页上限', async () => {
    const { ctx } = mountDrawer()
    assistantApi.providers.syncModels.mockResolvedValue({ data: { ...SYNC_OK, added: 3, unchanged: 0, truncated: true } })
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('分页上限'))
  })

  it('ok=false：error 提示且不刷新、不写成功摘要（不假成功）', async () => {
    const { ctx, emit } = mountDrawer()
    assistantApi.providers.syncModels.mockResolvedValue({
      data: { ...SYNC_OK, ok: false, added: 0, updated: 0, unchanged: 0, errors: ['同步写入失败，已整批回滚（OperationalError）'] },
    })
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('整批回滚'))
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(emit).not.toHaveBeenCalledWith('refresh')
    expect(ctx.syncResults[3].level).toBe('error')
  })

  it('HTTP 失败：拦截器提示，组件不重复弹错、不刷新、忙态复位', async () => {
    const { ctx, emit } = mountDrawer()
    assistantApi.providers.syncModels.mockRejectedValue(new Error('boom'))
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(emit).not.toHaveBeenCalledWith('refresh')
    expect(ctx.syncingProviderId.value).toBeNull()
    expect(ctx.syncResults[3]).toBeUndefined()
  })

  it('忙态防连点：同步中再次点击不发起第二个请求', async () => {
    const { ctx } = mountDrawer()
    let release
    assistantApi.providers.syncModels.mockReturnValue(new Promise((r) => { release = r }))
    const first = ctx.syncProviderModels({ id: 3, name: '智谱' })
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(assistantApi.providers.syncModels).toHaveBeenCalledTimes(1)
    release({ data: SYNC_OK })
    await first
    expect(ctx.syncingProviderId.value).toBeNull()
  })

  it('不同供应商各有独立 loading 键（可分别查询）', async () => {
    const { ctx } = mountDrawer()
    let releaseA
    assistantApi.providers.syncModels.mockReturnValue(new Promise((r) => { releaseA = r }))
    const p = ctx.syncProviderModels({ id: 3, name: 'A' })
    expect(ctx.syncingProviderId.value).toBe(3)
    releaseA({ data: SYNC_OK })
    await p
    expect(ctx.syncingProviderId.value).toBeNull()
  })
})

describe('覆盖手动/实测能力勾选（override_manual，终审 Important 3）', () => {
  it('默认 false：不弹确认，请求带 override_manual: false', async () => {
    const { ctx } = mountDrawer()
    assistantApi.providers.syncModels.mockResolvedValue({ data: SYNC_OK })
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(assistantApi.providers.syncModels).toHaveBeenCalledWith(3, { override_manual: false })
    expect(ctx.syncOverrideManual[3]).toBeFalsy()  // 勾选态默认关闭
  })

  it('勾选 true：先弹确认且文案明确警告覆盖手动修改与实测能力', async () => {
    const { ctx } = mountDrawer()
    assistantApi.providers.syncModels.mockResolvedValue({ data: SYNC_OK })
    ctx.syncOverrideManual[3] = true
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    const [message, title] = ElMessageBox.confirm.mock.calls[0]
    expect(message).toContain('手动')
    expect(message).toContain('实测')
    expect(title).toContain('覆盖')
    expect(assistantApi.providers.syncModels).toHaveBeenCalledWith(3, { override_manual: true })
    expect(ElMessage.success).toHaveBeenCalledWith('同步完成：新增 2 · 更新 1 · 未变 4 · 跳过 1')
  })

  it('确认框取消：不发同步请求、不写摘要、忙态复位', async () => {
    const { ctx } = mountDrawer()
    ElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
    ctx.syncOverrideManual[3] = true
    await ctx.syncProviderModels({ id: 3, name: '智谱' })
    expect(assistantApi.providers.syncModels).not.toHaveBeenCalled()
    expect(ctx.syncResults[3]).toBeUndefined()
    expect(ctx.syncingProviderId.value).toBeNull()
  })

  it('勾选仅作用于该供应商：其他供应商默认仍不覆盖', async () => {
    const { ctx } = mountDrawer()
    assistantApi.providers.syncModels.mockResolvedValue({ data: SYNC_OK })
    ctx.syncOverrideManual[3] = true
    await ctx.syncProviderModels({ id: 4, name: 'DeepSeek' })
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(assistantApi.providers.syncModels).toHaveBeenCalledWith(4, { override_manual: false })
  })
})

// ---------------- 模型列表：搜索与分批 ----------------
describe('模型列表搜索与分批渲染', () => {
  const manyModels = Array.from({ length: 25 }, (_, i) => ({ id: i + 1, model: `mod-${String(i).padStart(2, '0')}` }))
  const provider = { id: 3, name: 'p', models: manyModels }

  it('默认只渲染首批 20 条，展开后全量', () => {
    const { ctx } = mountDrawer([provider])
    expect(ctx.visibleModels(provider).length).toBe(20)
    expect(ctx.visibleModels(provider)[0].model).toBe('mod-00')
    expect(ctx.hiddenModelCount(provider)).toBe(5)
    ctx.expandModels(provider)
    expect(ctx.visibleModels(provider).length).toBe(25)
    expect(ctx.hiddenModelCount(provider)).toBe(0)
  })

  it('搜索按模型名过滤（大小写不敏感），与分批叠加', () => {
    const { ctx } = mountDrawer([provider])
    ctx.setModelQuery(provider, 'MOD-07')
    expect(ctx.visibleModels(provider).map((m) => m.model)).toEqual(['mod-07'])
    ctx.setModelQuery(provider, 'mod-2')
    // mod-20..mod-24 共 5 条（模型名为 mod-00..mod-24，无裸 mod-2），未超首批上限
    expect(ctx.visibleModels(provider).length).toBe(5)
    ctx.setModelQuery(provider, '不存在')
    expect(ctx.visibleModels(provider).length).toBe(0)
    ctx.setModelQuery(provider, '')
    expect(ctx.visibleModels(provider).length).toBe(20)
  })

  it('搜索词清空后恢复分批上限', () => {
    const { ctx } = mountDrawer([provider])
    ctx.expandModels(provider)
    ctx.setModelQuery(provider, 'mod-01')
    expect(ctx.visibleModels(provider).length).toBe(1)
    ctx.setModelQuery(provider, '')
    expect(ctx.visibleModels(provider).length).toBe(25)
  })
})

// ---------------- 供应商表单：预设与管控凭证 ----------------
describe('供应商表单保存 preset_key / models_url / 管控凭证', () => {
  it('保存透传 preset_key、models_url 与火山 AK/SK', async () => {
    const { ctx } = mountDrawer()
    assistantApi.providers.save.mockResolvedValue({ data: { provider: { id: 42 } } })
    ctx.openProviderForm()
    ctx.provDialog.name = '火山方舟'
    ctx.provDialog.preset_key = 'volcengine'
    ctx.provDialog.models_url = 'https://ark.cn-beijing.volces.com/api/v3'
    ctx.provDialog.sync_access_key = 'AK123'
    ctx.provDialog.sync_secret_key = 'SK456'
    await ctx.saveProvider()
    expect(assistantApi.providers.save).toHaveBeenCalledWith(expect.objectContaining({
      name: '火山方舟',
      preset_key: 'volcengine',
      models_url: 'https://ark.cn-beijing.volces.com/api/v3',
      sync_access_key: 'AK123',
      sync_secret_key: 'SK456',
    }))
  })

  it('编辑回填 preset_key/models_url，AK/SK 留空（保留现值）', () => {
    const { ctx } = mountDrawer()
    ctx.editProvider({ id: 5, name: '火山', protocol: 'openai', base_url: 'https://ark.cn-beijing.volces.com/api/v3', preset_key: 'volcengine', models_url: '', api_key: 'x', enabled: true, sync_credentials_set: true })
    expect(ctx.provDialog).toMatchObject({ id: 5, preset_key: 'volcengine', models_url: '', sync_access_key: '', sync_secret_key: '' })
    expect(ctx.provDialog.api_key).toBe('')
  })

  it('未选 ak_sk 套餐时也可留空 AK/SK（不发该字段由后端保留）', async () => {
    const { ctx } = mountDrawer()
    assistantApi.providers.save.mockResolvedValue({ data: { provider: { id: 1 } } })
    ctx.openProviderForm()
    ctx.provDialog.name = 'DeepSeek'
    ctx.provDialog.preset_key = 'deepseek'
    await ctx.saveProvider()
    const payload = assistantApi.providers.save.mock.calls[0][0]
    expect(payload.sync_access_key).toBeUndefined()
    expect(payload.sync_secret_key).toBeUndefined()
  })
})

// ---------------- 每模型实测与恢复同步值 ----------------
describe('每模型工具/图片实测与恢复同步值', () => {
  it('工具实测传该模型 model_id（不只测供应商第一个模型）', async () => {
    const { ctx } = mountDrawer()
    assistantApi.config.test.mockResolvedValue({
      data: { text: { ok: true, latency_ms: 10, reply: 'hi' }, tools: { ok: true, latency_ms: 12, tool_calls: ['calc'] } },
    })
    await ctx.testProvider({ id: 3, name: 'p' }, 'tools', { id: 7, model: 'glm-5.3' })
    expect(assistantApi.config.test).toHaveBeenCalledWith(expect.objectContaining({ provider_id: 3, test_tools: true, model_id: 7 }))
  })

  it('图片实测传该模型 model_id', async () => {
    const { ctx } = mountDrawer()
    assistantApi.config.test.mockResolvedValue({
      data: { text: { ok: true, latency_ms: 10, reply: 'hi' }, image: { ok: true, latency_ms: 30, reply: '一只猫' } },
    })
    await ctx.testProvider({ id: 3, name: 'p' }, 'image', { id: 8, model: 'kimi-k3' })
    expect(assistantApi.config.test).toHaveBeenCalledWith(expect.objectContaining({ provider_id: 3, include_image: true, model_id: 8 }))
  })

  it('verdict 三态落到结果行：未知不显示成 ❌', async () => {
    const { ctx } = mountDrawer()
    assistantApi.config.test.mockResolvedValue({
      data: { text: { ok: true, latency_ms: 10, reply: 'hi' }, tools: { ok: false, verdict: 'unknown', error: 'HTTP 429' } },
    })
    await ctx.testProvider({ id: 3, name: 'p' }, 'tools', { id: 7, model: 'm' })
    const result = ctx.provTestResult.value[3]
    expect(result.ok).toBe(false)
    expect(result.lines.join('\n')).toContain('HTTP 429')
    expect(result.lines.join('\n')).toContain('未确认')
  })

  it('恢复同步值：清除手动覆盖并按最近快照恢复', async () => {
    const { ctx, emit } = mountDrawer()
    assistantApi.models.resetOverrides.mockResolvedValue({ data: { ok: true, model_id: 7, cleared: ['supports_tools'], providers: [] } })
    await ctx.resetModelOverrides({ id: 7, model: 'glm-5.3', capability: { manual_fields: ['supports_tools'] } })
    expect(assistantApi.models.resetOverrides).toHaveBeenCalledWith(7)
    expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('已恢复'))
    expect(emit).toHaveBeenCalledWith('refresh')
  })

  it('没有手动覆盖字段时不发请求', async () => {
    const { ctx } = mountDrawer()
    await ctx.resetModelOverrides({ id: 7, model: 'm', capability: { manual_fields: [] } })
    expect(assistantApi.models.resetOverrides).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('没有手动覆盖'))
  })
})

// ---------------- 模板接线（无 DOM 环境的静态断言，沿用项目范式） ----------------
describe('模板接线：aria-live 摘要与延迟渲染', () => {
  const drawerSrc = readFileSync(new URL('../src/components/assistant/SettingsDrawer.vue', import.meta.url), 'utf8')

  it('同步摘要区使用 aria-live="polite"', () => {
    expect(drawerSrc).toMatch(/aria-live="polite"/)
  })

  it('首屏外模型卡使用 content-visibility:auto + contain-intrinsic-size', () => {
    expect(drawerSrc).toContain('content-visibility: auto')
    expect(drawerSrc).toContain('contain-intrinsic-size')
  })

  it('同步按钮带忙态 loading 与展开按钮为真实按钮（键盘可达）', () => {
    expect(drawerSrc).toMatch(/@click="syncProviderModels\(p\)"/)
    expect(drawerSrc).toMatch(/:loading="syncingProviderId === p\.id"/)
    // 展开按钮是真实 <el-button>（渲染为原生 button，可 Tab 聚焦、Enter/Space 触发）
    expect(drawerSrc).toMatch(/<el-button\s+v-if="hiddenModelCount\(p\) > 0"[\s\S]*?@click="expandModels\(p\)"/)
  })

  it('override_manual 勾选挂在供应商同步操作区（终审 Important 3：文档承诺的控件真实存在）', () => {
    // 勾选按供应商独立绑定（默认不勾选），开启时有明确警告文案
    expect(drawerSrc).toMatch(/<el-checkbox\s+v-model="syncOverrideManual\[p\.id\]"/)
    expect(drawerSrc).toContain('覆盖手动/实测')
    expect(drawerSrc).toContain('sync-override-warn')
  })

  it('文档链接带 rel="noopener noreferrer"', () => {
    expect(drawerSrc).toContain('rel="noopener noreferrer"')
  })
})
