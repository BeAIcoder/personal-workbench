import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { ElMessage } from 'element-plus'
import ChatPanel from '../src/components/assistant/ChatPanel.vue'
import { assistantApi } from '../src/api'

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
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
      history: vi.fn(),
      config: {
        ...actual.assistantApi.config,
        upload: vi.fn(),
        saveGlobals: vi.fn(),
      },
    },
  }
})

function mountChat() {
  return ChatPanel.setup(
    {
      sessionId: 's_upload_01',
      agents: [],
      selectedAgent: null,
      llmInfo: {},
      models: [],
      workModes: {},
      reasoning: 'default',
      teamConfig: {},
    },
    { expose: () => {}, emit: vi.fn() },
  )
}

function file(name, size = 128, type = '') {
  return { name, size, type }
}

function responseFor(raw) {
  const isImage = /\.(png|jpe?g|webp|gif)$/i.test(raw.name)
  const isDocument = /\.(pdf|docx?|pptx?)$/i.test(raw.name)
  return {
    data: {
      name: raw.name,
      path: `D:/isolated-uploads/${raw.name}`,
      kind: isImage ? 'image' : (isDocument ? 'document_text' : 'file'),
      size: raw.size,
      note: isDocument ? '已提取 100 字供分析' : '',
    },
  }
}

function dragEvent(files, types = ['Files']) {
  return {
    dataTransfer: { files, types },
    preventDefault: vi.fn(),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  assistantApi.config.upload.mockReset()
  assistantApi.config.upload.mockImplementation(async (_sessionId, raw) => responseFor(raw))
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:document-upload')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('聊天附件多入口上传', () => {
  it('拖放两个文件复用上传接口并按顺序进入附件区', async () => {
    const ctx = mountChat()
    const first = file('first.pdf')
    const second = file('second.docx')
    const event = dragEvent([first, second])
    expect(typeof ctx.onDrop).toBe('function')

    ctx.onDragEnter(event)
    expect(ctx.dragActive.value).toBe(true)
    await ctx.onDrop(event)

    expect(event.preventDefault).toHaveBeenCalled()
    expect(assistantApi.config.upload.mock.calls.map((call) => call[1])).toEqual([first, second])
    expect(ctx.attachments.value.map((item) => item.name)).toEqual(['first.pdf', 'second.docx'])
    expect(ctx.dragActive.value).toBe(false)
    expect(ctx.uploading.value).toBe(false)
  })

  it('粘贴含文件时接管事件并上传', async () => {
    const ctx = mountChat()
    const raw = file('pasted.png', 256, 'image/png')
    const event = { clipboardData: { files: [raw] }, preventDefault: vi.fn() }
    expect(typeof ctx.onPaste).toBe('function')

    await ctx.onPaste(event)

    expect(event.preventDefault).toHaveBeenCalledOnce()
    expect(assistantApi.config.upload).toHaveBeenCalledWith('s_upload_01', raw)
    expect(ctx.attachments.value[0].name).toBe('pasted.png')
    expect(ctx.attachments.value[0].previewUrl).toBe('blob:document-upload')
  })

  it('纯文本粘贴不拦截、不上传也不改写草稿', async () => {
    const ctx = mountChat()
    ctx.draft.value = '保留草稿'
    const event = {
      clipboardData: { files: [], getData: () => '普通文字' },
      preventDefault: vi.fn(),
    }

    await ctx.onPaste(event)

    expect(event.preventDefault).not.toHaveBeenCalled()
    expect(assistantApi.config.upload).not.toHaveBeenCalled()
    expect(ctx.draft.value).toBe('保留草稿')
  })

  it('非文件拖动不显示遮罩，嵌套区域拖入拖出保持稳定', () => {
    const ctx = mountChat()
    const textDrag = dragEvent([], ['text/plain'])
    ctx.onDragEnter(textDrag)
    expect(ctx.dragActive.value).toBe(false)

    const fileDrag = dragEvent([file('a.pdf')])
    ctx.onDragEnter(fileDrag)
    ctx.onDragEnter(fileDrag)
    ctx.onDragLeave(fileDrag)
    expect(ctx.dragActive.value).toBe(true)
    ctx.onDragLeave(fileDrag)
    expect(ctx.dragActive.value).toBe(false)
  })

  it('不支持类型和超过 10MB 都在请求前拦截', async () => {
    const ctx = mountChat()

    await ctx.uploadFiles([
      file('unsafe.exe'),
      file('too-large.pdf', 10 * 1024 * 1024 + 1),
    ])

    expect(assistantApi.config.upload).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledTimes(2)
    expect(ctx.attachments.value).toEqual([])
    expect(ctx.uploading.value).toBe(false)
  })

  it('无扩展名文件沿用后端口径允许上传', async () => {
    const ctx = mountChat()

    await ctx.uploadFiles([file('README')])

    expect(assistantApi.config.upload).toHaveBeenCalledOnce()
    expect(ctx.attachments.value[0].name).toBe('README')
  })

  it('批量中单个失败不影响后续文件，结束后复位 loading', async () => {
    const ctx = mountChat()
    assistantApi.config.upload
      .mockRejectedValueOnce(new Error('synthetic upload failure'))
      .mockImplementationOnce(async (_sessionId, raw) => responseFor(raw))

    await expect(ctx.uploadFiles([file('first.pdf'), file('second.pptx')])).resolves.toBeUndefined()

    expect(assistantApi.config.upload).toHaveBeenCalledTimes(2)
    expect(ctx.attachments.value.map((item) => item.name)).toEqual(['second.pptx'])
    expect(ctx.uploading.value).toBe(false)
  })

  it('并发批次中先结束的一批不会提前清除 loading', async () => {
    const ctx = mountChat()
    const pending = new Map()
    assistantApi.config.upload.mockImplementation((_sessionId, raw) => new Promise((resolve) => {
      pending.set(raw.name, () => resolve(responseFor(raw)))
    }))

    const firstBatch = ctx.uploadFiles([file('first.pdf')])
    const secondBatch = ctx.uploadFiles([file('second.pdf')])
    expect(ctx.uploading.value).toBe(true)

    pending.get('first.pdf')()
    await firstBatch
    expect(ctx.uploading.value).toBe(true)

    pending.get('second.pdf')()
    await secondBatch
    expect(ctx.uploading.value).toBe(false)
  })

  it('模板实际绑定拖放、粘贴和文件多选', () => {
    const source = readFileSync(new URL('../src/components/assistant/ChatPanel.vue', import.meta.url), 'utf8')
    expect(source).toMatch(/@dragover\.prevent/)
    expect(source).toMatch(/@drop="onDrop"/)
    expect(source).toMatch(/@paste="onPaste"/)
    expect(source).toMatch(/v-if="dragActive"/)
    expect(source).toMatch(/<el-upload[\s\S]*?multiple[\s\S]*?ASSISTANT_ACCEPT/)
  })
})
