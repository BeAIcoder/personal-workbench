// swarm4 任务 chat-stop-button：聊天生成中「停止」按钮回归（外部验证 L10 修复）。
// 与 bugfix-frontend 同范式的静态断言：无 jsdom/@vue/test-utils，读取 .vue 源码校验接线。
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const src = readFileSync(
  new URL('../src/components/assistant/ChatPanel.vue', import.meta.url),
  'utf8',
)

describe('聊天区停止生成按钮（L10 修复）', () => {
  it('生成中（loading）显示「停止」按钮并调用 abortStream', () => {
    expect(src).toMatch(/v-if="loading"[^>]*@click="abortStream\(\)"/)
    expect(src).toContain('>停止</el-button>')
  })

  it('非生成中显示「发送」按钮走 send()', () => {
    expect(src).toMatch(/v-else type="primary" @click="send\(\)">发送<\/el-button>/)
  })

  it('abortStream 由 AbortController 实现，且组件暴露供父级调用', () => {
    expect(src).toContain('streamCtrl.abort()')
    expect(src).toMatch(/defineExpose\(\{[^}]*abortStream/)
  })
})
