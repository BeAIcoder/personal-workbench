// Task 6C：知识库设置的密钥输入生命周期。
// 保存成功后必须清空弹窗里的 API Key 明文（已入库/有 hasKey 占位提示）；
// 保存失败必须保留输入，便于用户修正后重试。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'

const knowledgeApi = vi.hoisted(() => ({
  docs: vi.fn(),
  facets: vi.fn(),
  doc: vi.fn(),
  context: vi.fn(),
  file: vi.fn(),
  upload: vi.fn(),
  reindex: vi.fn(),
  remove: vi.fn(),
  settings: vi.fn(),
  saveSettings: vi.fn(),
  testEmbedding: vi.fn(),
  search: vi.fn(),
  stats: vi.fn(),
  rebuild: vi.fn(),
}))

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
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))

vi.mock('../src/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  API_BASE: '/api',
  knowledgeApi,
  toMessage: async (error) => String(error?.message || error),
}))

const DocLibrary = (await import('../src/components/knowledge/DocLibrary.vue')).default

function mount() {
  return DocLibrary.setup({}, { expose: () => {}, emit: () => {} })
}

beforeEach(() => {
  vi.resetAllMocks()
  knowledgeApi.docs.mockResolvedValue({ data: { items: [], total: 0 } })
  knowledgeApi.facets.mockResolvedValue({ data: { categories: [] } })
  knowledgeApi.settings.mockResolvedValue({
    data: {
      ocr_enabled: false,
      ocr_available: true,
      embedding_enabled: true,
      embedding_model: 'bge-m3',
      embedding_base_url: '',
      embedding_dim: 1024,
      has_api_key: true,
      vector_ready: true,
    },
  })
})

describe('知识库设置：保存后清空 Key 输入', () => {
  it('保存成功：提交当前 Key 后清空输入，hasKey 保持为真', async () => {
    const { ElMessage } = await import('element-plus')
    knowledgeApi.saveSettings.mockResolvedValueOnce({ data: { has_api_key: true } })
    const ctx = mount()

    await ctx.openSettings()
    ctx.settings.form.embedding_api_key = 'sk-synthetic-test-key'
    await ctx.saveSettings()

    expect(knowledgeApi.saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({ embedding_api_key: 'sk-synthetic-test-key' }),
    )
    expect(ctx.settings.form.embedding_api_key).toBe('')
    expect(ctx.settings.hasKey).toBe(true)
    expect(ElMessage.success).toHaveBeenCalledWith('设置已保存')
  })

  it('保存失败：不提示成功、保留用户输入供修正重试', async () => {
    const { ElMessage } = await import('element-plus')
    knowledgeApi.saveSettings.mockRejectedValueOnce(new Error('boom'))
    const ctx = mount()

    await ctx.openSettings()
    ctx.settings.form.embedding_api_key = 'sk-synthetic-test-key'
    await ctx.saveSettings()

    expect(ctx.settings.form.embedding_api_key).toBe('sk-synthetic-test-key')
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ctx.settings.saving).toBe(false)
  })

  it('未输入新 Key 时提交体不带 key（保持已保存值不变）', async () => {
    knowledgeApi.saveSettings.mockResolvedValueOnce({ data: { has_api_key: true } })
    const ctx = mount()

    await ctx.openSettings()
    expect(ctx.settings.form.embedding_api_key).toBe('')
    await ctx.saveSettings()

    const payload = knowledgeApi.saveSettings.mock.calls[0][0]
    expect(payload).not.toHaveProperty('embedding_api_key')
  })
})

// T2A 后端规则：自定义向量接口地址必须单独填写该地址的 API Key，绝不把聊天模型
// Key 发往自定义地址（embedding.py::CUSTOM_URL_KEY_REQUIRED）。前端占位/帮助必须
// 把这条规则讲清楚，否则用户会以为留空仍能复用聊天 Key。
describe('知识库设置：自定义地址的 Key 占位与帮助', () => {
  const CUSTOM_BASE = 'https://embed.example.com/v1'

  it('未填自定义地址且无已保存 Key：沿用「跟随当前激活供应商」提示', async () => {
    knowledgeApi.settings.mockResolvedValueOnce({
      data: { embedding_enabled: false, has_api_key: false, embedding_base_url: '' },
    })
    const ctx = mount()
    await ctx.openSettings()

    expect(ctx.keyPlaceholder.value).toBe('留空 = 跟随当前激活模型供应商')
  })

  it('填了自定义地址：占位提示必须填写专用 Key', async () => {
    knowledgeApi.settings.mockResolvedValueOnce({
      data: { embedding_enabled: true, has_api_key: false, embedding_base_url: CUSTOM_BASE },
    })
    const ctx = mount()
    await ctx.openSettings()

    expect(ctx.keyPlaceholder.value).toContain('自定义地址必须填写专用 Key')
    expect(ctx.keyPlaceholder.value).toContain('不会使用聊天模型 Key')
  })

  it('自定义地址 + 已有 Key：仍提示专用 Key 规则（换地址会清空旧 Key）', async () => {
    knowledgeApi.settings.mockResolvedValueOnce({
      data: { embedding_enabled: true, has_api_key: true, embedding_base_url: CUSTOM_BASE },
    })
    const ctx = mount()
    await ctx.openSettings()

    expect(ctx.keyPlaceholder.value).toContain('自定义地址必须填写专用 Key')
  })

  it('模板里给出常驻帮助文案（T2A 同口径）', () => {
    const src = readFileSync(
      new URL('../src/components/knowledge/DocLibrary.vue', import.meta.url),
      'utf8',
    )
    expect(src).toContain('自定义向量接口地址必须单独填写该地址的 API Key')
    expect(src).toContain('不会使用聊天模型 Key')
  })

  it('T2A 新增的 resolved/resolved_detail 字段不影响设置回填', async () => {
    knowledgeApi.settings.mockResolvedValueOnce({
      data: {
        embedding_enabled: true,
        has_api_key: false,
        embedding_base_url: CUSTOM_BASE,
        resolved: false,
        resolved_detail: '自定义向量接口地址必须单独填写该地址的 API Key（不会使用聊天模型 Key）',
      },
    })
    const ctx = mount()
    await ctx.openSettings()

    expect(ctx.settings.form.embedding_base_url).toBe(CUSTOM_BASE)
    expect(ctx.settings.form.embedding_api_key).toBe('')
    expect(ctx.settings.hasKey).toBe(false)
  })
})

// 后端 resolve_config_with_reason 已给出「为什么不可用」；前端不展示会让用户看到
// resolved=false 却不知道要补专用 Key 还是禁用项，测试连接/重建也难定位。
describe('设置弹窗展示向量不可用原因（resolved_detail）', () => {
  const REASON = '自定义向量接口地址必须单独填写该地址的 API Key（不会使用聊天模型 Key）'

  it('resolved=false：展示后端原因', async () => {
    knowledgeApi.settings.mockResolvedValueOnce({
      data: { embedding_enabled: true, has_api_key: false, embedding_base_url: 'https://embed.example.com/v1', resolved: false, resolved_detail: REASON },
    })
    const ctx = mount()
    await ctx.openSettings()

    expect(ctx.settings.resolved).toBe(false)
    expect(ctx.settings.resolvedDetail).toBe(REASON)
  })

  it('resolved=true：不展示原因', async () => {
    knowledgeApi.settings.mockResolvedValueOnce({
      data: { embedding_enabled: true, has_api_key: true, embedding_base_url: '', resolved: true, resolved_detail: '' },
    })
    const ctx = mount()
    await ctx.openSettings()

    expect(ctx.settings.resolved).toBe(true)
    expect(ctx.settings.resolvedDetail).toBe('')
  })

  it('保存后用响应刷新原因（保存后仍不可用时立即可见）', async () => {
    knowledgeApi.saveSettings.mockResolvedValueOnce({
      data: { has_api_key: false, resolved: false, resolved_detail: REASON },
    })
    const ctx = mount()
    await ctx.openSettings()
    ctx.settings.form.embedding_base_url = 'https://embed.example.com/v1'
    await ctx.saveSettings()

    expect(ctx.settings.resolvedDetail).toBe(REASON)
  })

  it('模板展示 resolvedDetail（警示条）', () => {
    const src = readFileSync(
      new URL('../src/components/knowledge/DocLibrary.vue', import.meta.url),
      'utf8',
    )
    expect(src).toContain('settings.resolvedDetail')
    expect(src).toContain('向量检索当前不可用')
  })
})

// 后端 rebuild_vectors 默认全量，但显式 limit/第 201 篇起会返回 remaining 并标记待继续；
// 前端只报 done/failed 会让用户以为 37 篇也已完成。
describe('向量重建展示分批进度并可续跑', () => {
  it('remaining=0：展示 processed/done/failed', async () => {
    const { ElMessage, ElMessageBox } = await import('element-plus')
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    knowledgeApi.rebuild.mockResolvedValueOnce({
      data: { vectors: { processed: 200, done: 198, failed: 2, remaining: 0 } },
    })
    const ctx = mount()

    await ctx.rebuildVectors()

    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    const msg = ElMessage.success.mock.calls[0][0]
    expect(msg).toContain('处理 200 篇')
    expect(msg).toContain('成功 198 篇')
    expect(msg).toContain('失败 2 篇')
    expect(ElMessage.warning).not.toHaveBeenCalled()
  })

  it('remaining>0：warning 明确剩余篇数并可再次点击续跑', async () => {
    const { ElMessage, ElMessageBox } = await import('element-plus')
    ElMessageBox.confirm.mockResolvedValueOnce('confirm')
    knowledgeApi.rebuild.mockResolvedValueOnce({
      data: { vectors: { processed: 200, done: 195, failed: 2, remaining: 37 } },
    })
    const ctx = mount()

    await ctx.rebuildVectors()

    expect(ElMessage.warning).toHaveBeenCalledTimes(1)
    const msg = ElMessage.warning.mock.calls[0][0]
    expect(msg).toContain('处理 200 篇')
    expect(msg).toContain('剩余 37 篇')
    expect(msg).toContain('续跑')
    expect(ElMessage.success).not.toHaveBeenCalled()
  })
})
