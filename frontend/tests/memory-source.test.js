// @vitest-environment jsdom
// 记忆来源文档（source_doc_id / source_doc_title）前端行为回归：
// 列表与编辑展示来源文档名、新建/编辑可用文档下拉选择来源或留空、
// 保存携带 source_doc_id（null = 未关联）、编辑后新建不残留来源、
// 文档下拉加载失败不阻断保存。后端契约：create/update 接受可选
// source_doc_id，列表返回 source_doc_id + source_doc_title，非法 id 由后端 4xx。
// 本文件只测前端：请求全部 mock（后端该契约由另一变更完成）。
import './jsdom-setup.js'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: '/api' },
}))
const knowledgeApi = vi.hoisted(() => ({ docs: vi.fn() }))
const memoryApi = vi.hoisted(() => ({
  list: vi.fn(),
  stats: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  importMd: vi.fn(),
}))

vi.mock('../src/api', () => ({ default: api, API_BASE: '/api', knowledgeApi, memoryApi }))
vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
  }
})

const { ElMessage } = await import('element-plus')
const MemoryPanel = (await import('../src/components/knowledge/MemoryPanel.vue')).default

const GLOBAL = { plugins: [ElementPlus] }
const DOC_ITEMS = [
  { id: 7, title: '手册A' },
  { id: 9, title: '手册B' },
]

function clickButton(wrapper, label) {
  const btn = wrapper.findAll('button').find((b) => b.text().trim() === label)
  expect(btn, `未找到按钮「${label}」`).toBeTruthy()
  return btn
}

function mountPanel(listItems = []) {
  memoryApi.list.mockResolvedValue({ data: { items: listItems } })
  memoryApi.stats.mockResolvedValue({ data: { total: listItems.length, by_source: {}, pinned: 0 } })
  knowledgeApi.docs.mockResolvedValue({ data: { items: DOC_ITEMS, total: DOC_ITEMS.length } })
  return mount(MemoryPanel, { global: GLOBAL })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockResolvedValue({ data: { items: [] } })
})

describe('列表与编辑展示来源文档名', () => {
  it('列表行渲染 source_doc_title', async () => {
    const wrapper = mountPanel([
      { id: 1, content: '记得下单备注', type: 'project', source: 'manual', source_doc_id: 7, source_doc_title: '手册A' },
    ])
    await flushPromises()

    expect(wrapper.text()).toContain('手册A')
  })

  it('无来源文档的行不误显示空标题', async () => {
    const wrapper = mountPanel([{ id: 2, content: '普通记忆', type: '', source: 'manual' }])
    await flushPromises()

    expect(wrapper.vm.memories).toHaveLength(1)
    expect(wrapper.vm.memories[0].source_doc_title).toBeUndefined()
  })
})

describe('新建/编辑弹窗的来源文档下拉', () => {
  it('打开新建弹窗即加载文档下拉选项', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    await clickButton(wrapper, '新建记忆').trigger('click')
    await flushPromises()

    expect(wrapper.vm.memDialog.visible).toBe(true)
    expect(knowledgeApi.docs).toHaveBeenCalledWith(expect.objectContaining({ page_size: 100 }))
    expect(wrapper.vm.sourceDocs).toHaveLength(2)
  })

  it('编辑已有来源的记忆：表单回填 source_doc_id', async () => {
    const wrapper = mountPanel([
      { id: 5, content: '原文', pinned: false, type: 'project', source_doc_id: 7, source_doc_title: '手册A' },
    ])
    await flushPromises()

    await clickButton(wrapper, '编辑').trigger('click')
    await flushPromises()

    expect(wrapper.vm.memDialog.visible).toBe(true)
    expect(wrapper.vm.memForm.source_doc_id).toBe(7)
  })
})

describe('保存携带 source_doc_id', () => {
  it('新建选择来源文档：create 携带数字 source_doc_id', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await clickButton(wrapper, '新建记忆').trigger('click')
    await flushPromises()

    wrapper.vm.memForm.content = '内容'
    wrapper.vm.memForm.source_doc_id = 7
    await clickButton(wrapper, '保存').trigger('click')
    await flushPromises()

    expect(memoryApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ content: '内容', source_doc_id: 7 }),
    )
    expect(ElMessage.success).toHaveBeenCalledWith('已保存')
  })

  it('新建不选来源：create 携带 null（不是 undefined 也不是空串）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await clickButton(wrapper, '新建记忆').trigger('click')
    await flushPromises()

    wrapper.vm.memForm.content = '内容'
    await clickButton(wrapper, '保存').trigger('click')
    await flushPromises()

    expect(memoryApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ content: '内容', source_doc_id: null }),
    )
  })

  it('编辑保留原来源：update 携带原 source_doc_id', async () => {
    const wrapper = mountPanel([
      { id: 5, content: '原文', pinned: false, type: 'project', source_doc_id: 7, source_doc_title: '手册A' },
    ])
    await flushPromises()
    await clickButton(wrapper, '编辑').trigger('click')
    await flushPromises()

    await clickButton(wrapper, '保存').trigger('click')
    await flushPromises()

    expect(memoryApi.update).toHaveBeenCalledWith(5, expect.objectContaining({ source_doc_id: 7 }))
  })

  it('编辑清空来源：update 显式携带 null 以取消关联', async () => {
    const wrapper = mountPanel([
      { id: 5, content: '原文', pinned: false, type: 'project', source_doc_id: 7, source_doc_title: '手册A' },
    ])
    await flushPromises()
    await clickButton(wrapper, '编辑').trigger('click')
    await flushPromises()

    wrapper.vm.memForm.source_doc_id = ''
    await clickButton(wrapper, '保存').trigger('click')
    await flushPromises()

    expect(memoryApi.update).toHaveBeenCalledWith(5, expect.objectContaining({ source_doc_id: null }))
  })
})

describe('编辑残留与异常收口', () => {
  it('编辑过带来源的记忆后点新建：不残留上一条的来源', async () => {
    const wrapper = mountPanel([
      { id: 5, content: '原文', pinned: false, type: 'project', source_doc_id: 7, source_doc_title: '手册A' },
    ])
    await flushPromises()
    await clickButton(wrapper, '编辑').trigger('click')
    await flushPromises()

    await clickButton(wrapper, '新建记忆').trigger('click')
    await flushPromises()

    expect(wrapper.vm.memDialog.id).toBeNull()
    expect(wrapper.vm.memForm.content).toBe('')
    expect(wrapper.vm.memForm.source_doc_id).toBe('')
  })

  it('文档下拉加载失败：不阻断保存', async () => {
    const wrapper = mountPanel()
    knowledgeApi.docs.mockRejectedValue(new Error('boom'))
    await flushPromises()
    await clickButton(wrapper, '新建记忆').trigger('click')
    await flushPromises()

    expect(wrapper.vm.sourceDocs).toHaveLength(0)

    wrapper.vm.memForm.content = '内容'
    await clickButton(wrapper, '保存').trigger('click')
    await flushPromises()

    expect(memoryApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ content: '内容', source_doc_id: null }),
    )
    expect(ElMessage.success).toHaveBeenCalledWith('已保存')
    // 下拉加载失败由拦截器提示，组件自身不得重复报错
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('保存失败：不伪成功、弹窗保持打开、锁复位', async () => {
    const wrapper = mountPanel([
      { id: 5, content: '原文', pinned: false, type: '', source_doc_id: 7, source_doc_title: '手册A' },
    ])
    memoryApi.update.mockRejectedValue(new Error('boom'))
    await flushPromises()
    await clickButton(wrapper, '编辑').trigger('click')
    await flushPromises()

    await clickButton(wrapper, '保存').trigger('click')
    await flushPromises()

    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(wrapper.vm.memDialog.visible).toBe(true)
    expect(wrapper.vm.memDialog.saving).toBe(false)
  })
})
