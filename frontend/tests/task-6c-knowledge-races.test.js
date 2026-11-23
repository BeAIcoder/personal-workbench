// Task 6C：知识库 / 长期记忆 / 目录浏览的旧响应竞态。
// 用户快速切换文档、搜索词或目录时，先发出的慢请求会后到并覆盖新结果。
// 统一用「请求序号」丢弃过期响应，loading/空态只由最新请求决定。
// node 环境无 DOM：mock 生命周期钩子、消息组件与 API，直接驱动编译后的 setup()。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive } from 'vue'

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
const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))
// MemoryPanel 已切到 memoryApi 封装（source_doc_id 契约），接缝同步补齐
const memoryApi = vi.hoisted(() => ({
  list: vi.fn(),
  stats: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  importMd: vi.fn(),
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
  default: api,
  API_BASE: '/api',
  knowledgeApi,
  memoryApi,
  toMessage: async (error) => String(error?.message || error),
}))

const DocLibrary = (await import('../src/components/knowledge/DocLibrary.vue')).default
const KnowledgeSearch = (await import('../src/components/knowledge/KnowledgeSearch.vue')).default
const DocDetailDrawer = (await import('../src/components/knowledge/DocDetailDrawer.vue')).default
const MemoryPanel = (await import('../src/components/knowledge/MemoryPanel.vue')).default
const DirectoryPicker = (await import('../src/components/DirectoryPicker.vue')).default

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function mount(component, props = {}) {
  return component.setup(props, { expose: () => {}, emit: () => {} })
}

// watch 回调 + await 链：让出一批微任务直到稳定
async function flush(times = 6) {
  for (let i = 0; i < times; i += 1) {
    await nextTick()
    await Promise.resolve()
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  knowledgeApi.facets.mockResolvedValue({ data: { categories: [] } })
  knowledgeApi.stats.mockResolvedValue({ data: {} })
})

describe('文档列表：慢的旧查询不得覆盖新查询', () => {
  it('A 慢 B 快时只显示 B 的列表与总数', async () => {
    const slow = deferred()
    const fast = deferred()
    knowledgeApi.docs.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(DocLibrary)

    const p1 = ctx.load(1)
    const p2 = ctx.load(2)
    fast.resolve({ data: { items: [{ id: 2, title: 'B' }], total: 1 } })
    await p2
    slow.resolve({ data: { items: [{ id: 1, title: 'A' }], total: 9 } })
    await p1

    expect(ctx.rows.value.map((r) => r.title)).toEqual(['B'])
    expect(ctx.total.value).toBe(1)
    expect(ctx.loading.value).toBe(false)
  })

  it('过期请求的失败不得清空最新结果', async () => {
    const slow = deferred()
    const fast = deferred()
    knowledgeApi.docs.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(DocLibrary)

    const p1 = ctx.load(1)
    const p2 = ctx.load(2)
    fast.resolve({ data: { items: [{ id: 2, title: 'B' }], total: 1 } })
    await p2
    slow.reject(new Error('boom'))
    await p1

    expect(ctx.rows.value.map((r) => r.title)).toEqual(['B'])
  })
})

describe('知识检索：旧搜索结果不得覆盖新结果', () => {
  it('A 慢 B 快时只显示 B 的命中与向量提示', async () => {
    const slow = deferred()
    const fast = deferred()
    knowledgeApi.search.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(KnowledgeSearch)

    ctx.query.value = '旧问题'
    const p1 = ctx.doSearch()
    ctx.query.value = '新问题'
    const p2 = ctx.doSearch()
    fast.resolve({ data: { items: [{ title: 'B', vector_note: '' }] } })
    await p2
    slow.resolve({ data: { items: [{ title: 'A', vector_note: '旧向量' }] } })
    await p1

    expect(ctx.results.value.map((r) => r.title)).toEqual(['B'])
    expect(ctx.vectorNote.value).toBe('')
    expect(ctx.searching.value).toBe(false)
  })

  it('过期搜索失败不得清空最新命中', async () => {
    const slow = deferred()
    const fast = deferred()
    knowledgeApi.search.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(KnowledgeSearch)

    ctx.query.value = '旧问题'
    const p1 = ctx.doSearch()
    ctx.query.value = '新问题'
    const p2 = ctx.doSearch()
    fast.resolve({ data: { items: [{ title: 'B' }] } })
    await p2
    slow.reject(new Error('boom'))
    await p1

    expect(ctx.results.value.map((r) => r.title)).toEqual(['B'])
  })
})

describe('文档详情：切换文档/章节时旧响应不得覆盖', () => {
  it('同一文档的慢加载不得覆盖后发起的快加载（切换文档走同一序号）', async () => {
    knowledgeApi.doc.mockResolvedValueOnce({ data: { title: '初始化', outline: [] } })
    const props = reactive({ modelValue: true, docId: 1, chunkId: null })
    const ctx = mount(DocDetailDrawer, props)
    await Promise.resolve()
    await Promise.resolve()

    const slow = deferred()
    const fast = deferred()
    knowledgeApi.doc.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const p1 = ctx.load()
    const p2 = ctx.load()
    fast.resolve({ data: { title: 'B', outline: [] } })
    await p2
    slow.resolve({ data: { title: 'A', outline: [] } })
    await p1

    expect(ctx.detail.value.title).toBe('B')
    expect(ctx.loading.value).toBe(false)
  })

  it('慢的旧章节上下文不得覆盖新章节', async () => {
    knowledgeApi.doc.mockResolvedValueOnce({ data: { title: '初始化', outline: [] } })
    const props = reactive({ modelValue: true, docId: 5, chunkId: null })
    const ctx = mount(DocDetailDrawer, props)
    await Promise.resolve()
    await Promise.resolve()

    const slow = deferred()
    const fast = deferred()
    knowledgeApi.context.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const p1 = ctx.loadChunk(1)
    const p2 = ctx.loadChunk(2)
    fast.resolve({ data: { heading: '第二章', items: [{ chunk_id: 2 }] } })
    await p2
    slow.resolve({ data: { heading: '第一章', items: [{ chunk_id: 1 }] } })
    await p1

    expect(ctx.context.value.heading).toBe('第二章')
  })

  it('从带 chunk 的文档 A 切到无 chunk 的文档 B，A 的 chunk 迟到也不得显示', async () => {
    const slowChunk = deferred()
    knowledgeApi.doc.mockResolvedValueOnce({ data: { title: 'A', outline: [] } })
    knowledgeApi.context.mockReturnValueOnce(slowChunk.promise)
    const props = reactive({ modelValue: true, docId: 1, chunkId: 5 })
    const ctx = mount(DocDetailDrawer, props)
    await flush()
    expect(knowledgeApi.context).toHaveBeenCalledWith(1, 5, { before: 1, after: 1 })

    knowledgeApi.doc.mockResolvedValueOnce({ data: { title: 'B', outline: [] } })
    props.docId = 2
    props.chunkId = null
    await flush()
    expect(ctx.detail.value.title).toBe('B')

    slowChunk.resolve({ data: { heading: 'A 的命中上下文', items: [{ chunk_id: 5 }] } })
    await flush()

    expect(ctx.context.value).toBeNull()
  })

  it('文档详情在途时用户手点大纲 chunk，旧 load 不得用旧 prop 覆盖', async () => {
    const slowDoc = deferred()
    knowledgeApi.doc.mockReturnValueOnce(slowDoc.promise)
    const props = reactive({ modelValue: true, docId: 1, chunkId: 5 })
    const ctx = mount(DocDetailDrawer, props)

    knowledgeApi.context.mockResolvedValueOnce({
      data: { heading: '用户点选章节', items: [{ chunk_id: 9 }] },
    })
    await ctx.openChunk({ chunk_id: 9 })
    await flush()
    expect(ctx.context.value.heading).toBe('用户点选章节')

    // 若旧 load 仍按 props.chunkId=5 自动加载，会走到这个响应并覆盖用户选择
    knowledgeApi.context.mockResolvedValueOnce({
      data: { heading: '旧 prop 自动加载', items: [{ chunk_id: 5 }] },
    })
    slowDoc.resolve({ data: { title: 'A', outline: [] } })
    await flush()

    expect(ctx.context.value.heading).toBe('用户点选章节')
  })

  it('关闭抽屉后，在途 chunk 响应不得写入', async () => {
    const slowChunk = deferred()
    knowledgeApi.doc.mockResolvedValueOnce({ data: { title: 'A', outline: [] } })
    knowledgeApi.context.mockReturnValueOnce(slowChunk.promise)
    const props = reactive({ modelValue: true, docId: 1, chunkId: 5 })
    const ctx = mount(DocDetailDrawer, props)
    await flush()

    props.modelValue = false
    await flush()
    slowChunk.resolve({ data: { heading: '关闭后迟到的上下文', items: [{ chunk_id: 5 }] } })
    await flush()

    expect(ctx.context.value).toBeNull()
  })

  it('docId 被清空后，在途 chunk 响应不得写入', async () => {
    const slowChunk = deferred()
    knowledgeApi.doc.mockResolvedValueOnce({ data: { title: 'A', outline: [] } })
    knowledgeApi.context.mockReturnValueOnce(slowChunk.promise)
    const props = reactive({ modelValue: true, docId: 1, chunkId: 5 })
    const ctx = mount(DocDetailDrawer, props)
    await flush()

    props.docId = null
    props.chunkId = null
    await flush()
    slowChunk.resolve({ data: { heading: '无文档时迟到的上下文', items: [{ chunk_id: 5 }] } })
    await flush()

    expect(ctx.context.value).toBeNull()
  })
})

describe('长期记忆：关键词/类型切换的旧响应不得覆盖', () => {
  it('A 慢 B 快时只显示 B 的记忆列表', async () => {
    const slow = deferred()
    const fast = deferred()
    memoryApi.list.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(MemoryPanel)

    ctx.memKeyword.value = '旧'
    const p1 = ctx.loadMemories()
    ctx.memKeyword.value = '新'
    const p2 = ctx.loadMemories()
    fast.resolve({ data: { items: [{ id: 2, content: 'B' }] } })
    await p2
    slow.resolve({ data: { items: [{ id: 1, content: 'A' }] } })
    await p1

    expect(ctx.memories.value.map((m) => m.content)).toEqual(['B'])
  })
})

describe('目录浏览：快速进入不同目录时旧响应不得覆盖', () => {
  it('A 目录慢 B 目录快时路径与列表都取 B', async () => {
    const slow = deferred()
    const fast = deferred()
    api.get.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(DirectoryPicker, { modelValue: true })

    const p1 = ctx.load('/a')
    const p2 = ctx.load('/b')
    fast.resolve({ data: { path: '/b', parent: '/', entries: [{ name: 'b-child', path: '/b/b-child' }] } })
    await p2
    slow.resolve({ data: { path: '/a', parent: '/', entries: [{ name: 'a-child', path: '/a/a-child' }] } })
    await p1

    expect(ctx.currentPath.value).toBe('/b')
    expect(ctx.pathInput.value).toBe('/b')
    expect(ctx.entries.value.map((e) => e.name)).toEqual(['b-child'])
    expect(ctx.loading.value).toBe(false)
  })

  it('T5 根白名单拒绝（403）时不崩溃、loading 复位并保留上一次有效目录', async () => {
    api.get.mockResolvedValueOnce({
      data: { path: '/allowed', parent: '/', entries: [{ name: 'ok', path: '/allowed/ok' }] },
    })
    const ctx = mount(DirectoryPicker, { modelValue: true })
    await ctx.load('/allowed')

    api.get.mockRejectedValueOnce({
      response: { status: 403, data: { detail: '目录不在允许浏览的白名单内' } },
    })
    await ctx.load('/outside')

    expect(ctx.currentPath.value).toBe('/allowed')
    expect(ctx.entries.value.map((e) => e.name)).toEqual(['ok'])
    expect(ctx.loading.value).toBe(false)
  })
})

describe('辅助统计（facets/stats）旧响应不得覆盖', () => {
  it('DocLibrary：分类选项取最新一次 facets', async () => {
    const slow = deferred()
    const fast = deferred()
    knowledgeApi.facets.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(DocLibrary)

    const p1 = ctx.loadFacets()
    const p2 = ctx.loadFacets()
    fast.resolve({ data: { categories: [{ value: '新分类' }] } })
    await p2
    slow.resolve({ data: { categories: [{ value: '旧分类' }] } })
    await p1

    expect(ctx.categoryOptions.value).toContain('新分类')
    expect(ctx.categoryOptions.value).not.toContain('旧分类')
  })

  it('KnowledgeSearch：统计卡片取最新一次 stats', async () => {
    const slow = deferred()
    const fast = deferred()
    knowledgeApi.stats.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(KnowledgeSearch)

    const p1 = ctx.loadStats()
    const p2 = ctx.loadStats()
    fast.resolve({ data: { notes_indexed: 2, documents: { docs: 2 } } })
    await p2
    slow.resolve({ data: { notes_indexed: 9, documents: { docs: 9 } } })
    await p1

    expect(ctx.stats.value.notes_indexed).toBe(2)
    expect(ctx.stats.value.documents.docs).toBe(2)
  })

  it('MemoryPanel：记忆统计取最新一次响应', async () => {
    const slow = deferred()
    const fast = deferred()
    memoryApi.stats.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const ctx = mount(MemoryPanel)

    const p1 = ctx.loadMemStats()
    const p2 = ctx.loadMemStats()
    fast.resolve({ data: { total: 2, by_source: {}, by_type: {}, pinned: 0, top_hits: [] } })
    await p2
    slow.resolve({ data: { total: 9, by_source: {}, by_type: {}, pinned: 0, top_hits: [] } })
    await p1

    expect(ctx.memStats.total).toBe(2)
  })
})
