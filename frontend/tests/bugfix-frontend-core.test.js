// 批次 frontend-core 的回归测试：每条 bug 对应一组用例，纯函数直接 import 自 src/utils
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'

// B8 用：拦截器测试需 stub 掉 ElMessage（node 环境无 DOM），hoist 到模块级
vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))

import { ElMessage } from 'element-plus'
import api from '../src/api'
import { parseNotesQuery, pickAfterCreate } from '../src/utils/notes'
import { openCreateMem } from '../src/utils/knowledge'
import { buildServerUpdatePayload } from '../src/utils/mcp'
import { canSend, sendBlockHint, splitPreviewUrls } from '../src/utils/assistant'
import { importAllNeedsConfirm } from '../src/utils/expertPack'

describe('B1 新建笔记后的选中目标（pickAfterCreate）', () => {
  it('新建笔记在当前列表中 → 选中新建的那条', () => {
    const rows = [{ id: 1, title: 'a' }, { id: 2, title: 'b' }, { id: 3, title: 'c' }]
    expect(pickAfterCreate(rows, 2)).toEqual({ id: 2, title: 'b' })
  })

  it('筛选/翻页导致新建笔记不在列表中 → 返回 null，绝不退化为第一条（避免覆盖他人笔记）', () => {
    const rows = [{ id: 1, title: 'a' }, { id: 2, title: 'b' }]
    expect(pickAfterCreate(rows, 99)).toBeNull()
  })

  it('空列表 / 空入参安全返回 null', () => {
    expect(pickAfterCreate([], 1)).toBeNull()
    expect(pickAfterCreate(null, 1)).toBeNull()
    expect(pickAfterCreate(undefined, undefined)).toBeNull()
  })
})

describe('B3 /notes 路由参数解析（parseNotesQuery）', () => {
  it('highlight=<id>（知识库 RAG 命中链接）→ 解析出目标笔记 id', () => {
    expect(parseNotesQuery({ highlight: '42' })).toEqual({ q: null, highlight: 42 })
  })

  it('q 与 highlight 可同时存在', () => {
    expect(parseNotesQuery({ q: '报销', highlight: '7' })).toEqual({ q: '报销', highlight: 7 })
  })

  it('只有 q 时 highlight 为 null；空 query 全部为空', () => {
    expect(parseNotesQuery({ q: '报销' })).toEqual({ q: '报销', highlight: null })
    expect(parseNotesQuery({})).toEqual({ q: null, highlight: null })
    expect(parseNotesQuery({ highlight: '' })).toEqual({ q: null, highlight: null })
  })

  it('highlight 非法值（非数字）→ null，不选中任何笔记', () => {
    expect(parseNotesQuery({ highlight: 'abc' })).toEqual({ q: null, highlight: null })
  })
})

describe('B2 新建记忆重置表单（openCreateMem）', () => {
  it('编辑过 A 再点新建 → id 置 null、表单清空，保存不再覆盖 A', () => {
    // 模拟「编辑过 A」后的状态
    const dialog = { visible: false, id: 12, saving: false }
    const form = { content: 'A 的内容', pinned: true }
    openCreateMem(dialog, form)
    expect(dialog.id).toBeNull()
    expect(dialog.visible).toBe(true)
    expect(form.content).toBe('')
    expect(form.pinned).toBe(false)
  })

  it('连续新建：幂等，仍是空表单', () => {
    const dialog = { visible: true, id: null, saving: false }
    const form = { content: '输入到一半', pinned: false }
    openCreateMem(dialog, form)
    expect(dialog.id).toBeNull()
    expect(form.content).toBe('')
  })
})

describe('B4 编辑 MCP 服务器的保存 payload（buildServerUpdatePayload）', () => {
  it('envText 未被用户改动 → payload 不含 env 键（后端 exclude_unset，保留原值）', () => {
    const payload = buildServerUpdatePayload(
      { command: 'npx', args: ['-y', 'srv'], env: {}, enabled: true },
      { envTouched: false },
    )
    expect(payload).toEqual({ command: 'npx', args: ['-y', 'srv'], enabled: true })
    expect('env' in payload).toBe(false)
  })

  it('用户改过 env（含清空为 {}）→ payload 带上 env', () => {
    const payload = buildServerUpdatePayload(
      { command: 'npx', args: [], env: { API_KEY: 'x' }, enabled: false },
      { envTouched: true },
    )
    expect(payload).toEqual({ command: 'npx', args: [], env: { API_KEY: 'x' }, enabled: false })
    const cleared = buildServerUpdatePayload(
      { command: 'npx', args: [], env: {}, enabled: true },
      { envTouched: true },
    )
    expect(cleared.env).toEqual({})
  })
})

describe('B5 聊天发送门槛（canSend / sendBlockHint）', () => {
  const img = { name: 'a.png', kind: 'image', path: '/tmp/a.png', previewUrl: 'blob:x' }

  it('有文字 → 可发送', () => {
    expect(canSend('你好', [])).toBe(true)
    expect(canSend('你好', [img])).toBe(true)
    expect(sendBlockHint('你好', [])).toBe('')
  })

  it('空文本 + 附件：后端 message min_length=1 不支持只发附件 → 不可发送且必须给明确提示（不静默吞掉）', () => {
    expect(canSend('', [img])).toBe(false)
    const hint = sendBlockHint('', [img])
    expect(typeof hint).toBe('string')
    expect(hint.length).toBeGreaterThan(0)
  })

  it('空文本 + 无附件 → 静默忽略（没有可发内容，无需打扰）', () => {
    expect(canSend('', [])).toBe(false)
    expect(canSend('   ', [])).toBe(false)
    expect(sendBlockHint('', [])).toBe('')
    expect(sendBlockHint('   ', null)).toBe('')
  })
})

describe('B6 切换/新建会话时回收缩略图 objectURL（splitPreviewUrls）', () => {
  it('待发送附件的 previewUrl 必须保留（上传响应无 url 时它是唯一显示源），已入气泡的才回收', () => {
    const all = ['blob:bubble-1', 'blob:bubble-2', 'blob:pending-1']
    const pending = ['blob:pending-1']
    const { revoke, keep } = splitPreviewUrls(all, pending)
    expect(revoke).toEqual(['blob:bubble-1', 'blob:bubble-2'])
    expect(keep).toEqual(['blob:pending-1'])
  })

  it('无待发附件 → 全部回收；无已建 URL → 无操作', () => {
    expect(splitPreviewUrls(['blob:a'], [])).toEqual({ revoke: ['blob:a'], keep: [] })
    expect(splitPreviewUrls([], ['blob:a'])).toEqual({ revoke: [], keep: [] })
  })

  it('空值安全', () => {
    expect(splitPreviewUrls(null, null)).toEqual({ revoke: [], keep: [] })
    expect(splitPreviewUrls(['blob:a', null, ''], [undefined])).toEqual({ revoke: ['blob:a'], keep: [] })
  })
})

describe('B7 「全部导入」前置确认条件（importAllNeedsConfirm）', () => {
  const experts = [
    { name: 'a', is_builtin: false },
    { name: 'b', is_builtin: true },
  ]

  it('存在任一「有差异」的有效包 → 需要确认（将覆盖界面修改）', () => {
    const packs = [
      { name: 'a', valid: true, imported: true, diff: '' },
      { name: 'c', valid: true, imported: true, diff: 'prompt 不同' },
    ]
    expect(importAllNeedsConfirm(packs, experts)).toBe(true)
  })

  it('存在任一对应「内置」数据库专家的包 → 需要确认', () => {
    const packs = [{ name: 'b', valid: true, imported: true, diff: '' }]
    expect(importAllNeedsConfirm(packs, experts)).toBe(true)
  })

  it('全部无差异且非内置 → 不确认，直接导入', () => {
    const packs = [
      { name: 'a', valid: true, imported: true, diff: '' },
      { name: 'newbie', valid: true, imported: false, diff: '' },
    ]
    expect(importAllNeedsConfirm(packs, experts)).toBe(false)
  })

  it('文件错误的包不参与判断；空列表安全', () => {
    const packs = [{ name: 'b', valid: false, error: 'YAML 解析失败' }]
    expect(importAllNeedsConfirm(packs, experts)).toBe(false)
    expect(importAllNeedsConfirm([], [])).toBe(false)
    expect(importAllNeedsConfirm(null, null)).toBe(false)
  })
})

describe('B8 错误提示唯一出口：axios 响应拦截器', () => {
  it('一次请求失败 → ElMessage.error 只调一次（代表场景：McpView 保存失败）', async () => {
    // 模拟 axios 请求被拒绝（后端 500 + detail）
    api.defaults.adapter = (config) =>
      Promise.reject({
        config,
        isAxiosError: true,
        message: 'Request failed with status code 500',
        response: { status: 500, statusText: 'Server Error', headers: {}, config, data: { detail: '服务器错误' } },
      })
    ElMessage.error.mockClear()
    await expect(api.put('/mcp/servers/1', { command: 'x' })).rejects.toBeTruthy()
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledWith('服务器错误')
  })

  it('视图层 catch 只做状态恢复，不再读 e?.response 重复弹（源码回归守卫）', () => {
    const root = fileURLToPath(new URL('..', import.meta.url))
    const files = [
      'src/views/McpView.vue',
      'src/views/KnowledgeView.vue',
      'src/views/AssistantView.vue',
    ]
    for (const f of files) {
      const source = readFileSync(root + '/' + f, 'utf8')
      // 逐个 catch 窗口检查：重复弹窗的 bug 形态是 catch 里 ElMessage.error(e?.response?...)
      // （本地校验报错如 JSON 解析失败不含 .response，不受影响）
      let idx = -1
      while ((idx = source.indexOf('catch', idx + 1)) >= 0) {
        const win = source.slice(idx, idx + 200)
        const doubleToast = win.includes('ElMessage.error') && win.includes('.response')
        expect(doubleToast, `${f} 的 catch（偏移 ${idx}）存在重复弹窗`).toBe(false)
      }
    }
  })
})
