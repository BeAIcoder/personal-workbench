// assistant-chat 区域 E2E 探测回归测试。
// 1) CHAT-ERR-002：删除当前会话的 API 失败时，旧实现先清空 currentSessionId/messages，
//    导致「删除失败但聊天区已空、选中丢失」；应在删除成功后才分离当前会话。
// 2) CHAT-ERR-001 / 重命名：归档/恢复/重命名失败缺少 catch，产生未处理 Promise 拒绝。
// 3) CHAT-ATT-005：附件上传失败只有 try/finally，拒绝继续冒泡成 pageerror；应吞掉并复位 loading。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { assistantApi } from '../src/api'
import { ElMessage, ElMessageBox } from 'element-plus'
import AssistantView from '../src/views/AssistantView.vue'
import ChatPanel from '../src/components/assistant/ChatPanel.vue'

// node 环境无 DOM：mock 生命周期钩子与 API，直接驱动编译后的 setup()
vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: () => {},
    onBeforeUnmount: () => {},
  }
})

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))

vi.mock('../src/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    assistantApi: {
      ...actual.assistantApi,
      config: {
        ...actual.assistantApi.config,
        upload: vi.fn(),
      },
      sessions: {
        list: vi.fn(),
        create: vi.fn(),
        rename: vi.fn(),
        remove: vi.fn(),
        archive: vi.fn(),
        unarchive: vi.fn(),
      },
    },
  }
})

function mountView(session, current) {
  const ctx = AssistantView.setup({}, { expose: () => {}, emit: () => {} })
  ctx.sessions.value = [session]
  ctx.currentSessionId.value = current
  const chat = { abortStream: vi.fn(), clearMessages: vi.fn(), loadHistory: vi.fn() }
  ctx.chatRef.value = chat
  return { ctx, chat }
}

function mountChat(sessionId = 's_chat_01') {
  return ChatPanel.setup(
    {
      sessionId,
      agents: [],
      selectedAgent: null,
      llmInfo: {},
      models: [],
      workModes: {},
      reasoning: 'default',
    },
    { expose: () => {}, emit: () => {} },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  assistantApi.sessions.list.mockResolvedValue({ data: { sessions: [] } })
})

describe('CHAT-ERR-002 删除当前会话失败保持状态', () => {
  it('删除失败：不清空当前会话/消息/选中，不刷新列表也不提示成功', async () => {
    const session = { session_id: 's_delete_fail', title: '删除失败-探测' }
    const { ctx, chat } = mountView(session, session.session_id)
    ElMessageBox.confirm.mockResolvedValue('confirm')
    assistantApi.sessions.remove.mockRejectedValue(new Error('Network Error'))

    await expect(ctx.deleteSession(session)).resolves.toBeUndefined()

    expect(ctx.currentSessionId.value).toBe('s_delete_fail')
    // 删除当前会话前会先打断流，但失败时不得清空当前聊天状态
    expect(chat.abortStream).toHaveBeenCalledTimes(1)
    expect(chat.clearMessages).not.toHaveBeenCalled()
    expect(assistantApi.sessions.list).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
  })
})

describe('CHAT-ERR-001 归档/恢复/重命名失败不产生未处理拒绝', () => {
  it('归档失败：保留当前会话与聊天区，不提示成功', async () => {
    const session = { session_id: 's_archive_fail', title: '归档失败-探测' }
    const { ctx, chat } = mountView(session, session.session_id)
    assistantApi.sessions.archive.mockRejectedValue(new Error('Network Error'))

    await expect(ctx.archiveSession(session)).resolves.toBeUndefined()

    expect(ctx.currentSessionId.value).toBe('s_archive_fail')
    expect(chat.clearMessages).not.toHaveBeenCalled()
    expect(assistantApi.sessions.list).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('恢复失败：不提示成功、不刷新列表', async () => {
    const session = { session_id: 's_restore_fail', title: '恢复失败-探测' }
    const { ctx } = mountView(session, '')
    assistantApi.sessions.unarchive.mockRejectedValue(new Error('Network Error'))

    await expect(ctx.restoreSession(session)).resolves.toBeUndefined()

    expect(assistantApi.sessions.list).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
  })

  it('重命名失败：本地标题保持不变，不提示成功', async () => {
    const session = { session_id: 's_rename_fail', title: '旧标题' }
    const { ctx } = mountView(session, '')
    ElMessageBox.prompt.mockResolvedValue({ value: '新标题' })
    assistantApi.sessions.rename.mockRejectedValue(new Error('Network Error'))

    await expect(ctx.renameSession(session)).resolves.toBeUndefined()

    expect(session.title).toBe('旧标题')
    expect(ElMessage.success).not.toHaveBeenCalled()
  })
})

describe('CHAT-ATT-005 附件上传失败不产生未处理拒绝', () => {
  it('上传失败：onFilePicked 正常收敛、loading 复位、不残留附件', async () => {
    const ctx = mountChat()
    assistantApi.config.upload.mockRejectedValue(new Error('Network Error'))

    await expect(
      ctx.onFilePicked({ raw: { name: 'probe-fail.txt', size: 4, type: 'text/plain' } }),
    ).resolves.toBeUndefined()

    expect(ctx.uploading.value).toBe(false)
    expect(ctx.attachments.value).toEqual([])
  })
})
