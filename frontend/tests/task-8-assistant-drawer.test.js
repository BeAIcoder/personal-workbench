// Task 8 Fix round 1 / I-2：窄屏助手左列必须是真正的抽屉：
// 关闭态不可聚焦（inert/visibility）、打开聚焦首个会话、Esc 关闭回焦按钮、选中会话自动收起。
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

const hooks = vi.hoisted(() => ({ mounted: [], beforeUnmount: [] }))
const api = vi.hoisted(() => ({
  get: vi.fn().mockResolvedValue({ data: {} }),
  post: vi.fn(),
  defaults: { baseURL: '/api' },
}))

vi.mock('vue', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useSSRContext: () => ({ modules: new Set() }),
    onMounted: (fn) => hooks.mounted.push(fn),
    onUnmounted: (fn) => hooks.beforeUnmount.push(fn),
    onBeforeUnmount: (fn) => hooks.beforeUnmount.push(fn),
  }
})
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {}, path: '/assistant' }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() },
}))
vi.mock('../src/api', () => ({
  default: api,
  BACKEND_HINT: '测试后端提示',
  assistantApi: {
    config: { get: vi.fn(), saveGlobals: vi.fn() },
    team: vi.fn().mockResolvedValue({ data: { agents: [], llm: {}, presets: [], providers: [], models: [] } }),
    sessions: { list: vi.fn().mockResolvedValue({ data: { sessions: [] } }) },
  },
}))

const { drawerClosed, escClosesDrawer } = await import('../src/utils/assistantDrawer.js')
const AssistantView = (await import('../src/views/AssistantView.vue')).default
const viewSource = readFileSync(new URL('../src/views/AssistantView.vue', import.meta.url), 'utf8')
const sessionSource = readFileSync(new URL('../src/components/assistant/SessionList.vue', import.meta.url), 'utf8')

function mount() {
  return AssistantView.setup({}, { expose: () => {}, emit: () => {} })
}

describe('窄屏抽屉纯状态', () => {
  it('只在窄屏且未打开时视为关闭；宽屏始终可见', () => {
    expect(drawerClosed(true, false)).toBe(true)
    expect(drawerClosed(true, true)).toBe(false)
    expect(drawerClosed(false, false)).toBe(false)
    expect(drawerClosed(false, true)).toBe(false)
  })

  it('全局 Esc 只在窄屏打开且焦点在抽屉内时关闭', () => {
    const inside = { id: 'inside' }
    const drawer = { contains: (el) => el === inside }
    expect(escClosesDrawer({ narrow: true, open: true, drawer, active: inside })).toBe(true)
    expect(escClosesDrawer({ narrow: true, open: true, drawer, active: { id: 'outside' } })).toBe(false)
    expect(escClosesDrawer({ narrow: false, open: true, drawer, active: inside })).toBe(false)
    expect(escClosesDrawer({ narrow: true, open: false, drawer, active: inside })).toBe(false)
    expect(escClosesDrawer({ narrow: true, open: true, drawer: null, active: inside })).toBe(false)
  })
})

afterEach(() => vi.unstubAllGlobals())

describe('窄屏抽屉焦点与收起', () => {
  it('打开聚焦首个会话，Esc 关闭并把焦点还给按钮', async () => {
    const ctx = mount()
    ctx.narrow.value = true
    const firstSession = { focus: vi.fn() }
    const toggle = { focus: vi.fn() }
    ctx.leftColRef.value = { querySelector: vi.fn(() => firstSession) }
    ctx.leftToggleRef.value = toggle

    ctx.openDrawer()
    await nextTick()
    expect(ctx.leftOpen.value).toBe(true)
    expect(ctx.drawerClosed.value).toBe(false)
    expect(ctx.leftColRef.value.querySelector).toHaveBeenCalledWith('.session-main')
    expect(firstSession.focus).toHaveBeenCalledTimes(1)

    ctx.onDrawerEsc()
    await nextTick()
    expect(ctx.leftOpen.value).toBe(false)
    expect(ctx.drawerClosed.value).toBe(true)
    expect(toggle.focus).toHaveBeenCalledTimes(1)
  })

  it('全局 Esc 只在抽屉含焦点时关闭，焦点在外部时不误关', async () => {
    const ctx = mount()
    ctx.narrow.value = true
    ctx.leftOpen.value = true
    ctx.leftToggleRef.value = { focus: vi.fn() }
    const inside = { id: 'inside' }
    ctx.leftColRef.value = { contains: (el) => el === inside }

    vi.stubGlobal('document', { activeElement: inside })
    ctx.onWindowKeydown({ key: 'Escape' })
    await nextTick()
    expect(ctx.leftOpen.value).toBe(false)

    ctx.leftOpen.value = true
    vi.stubGlobal('document', { activeElement: { id: 'outside' } })
    ctx.onWindowKeydown({ key: 'Escape' })
    await nextTick()
    expect(ctx.leftOpen.value).toBe(true)
  })

  it('窄屏选中会话后自动收起；宽屏不收起', async () => {
    const ctx = mount()
    ctx.narrow.value = true
    ctx.leftOpen.value = true
    ctx.leftToggleRef.value = { focus: vi.fn() }
    ctx.onSelectSession('session-1')
    await nextTick()
    expect(ctx.currentSessionId.value).toBe('session-1')
    expect(ctx.leftOpen.value).toBe(false)

    ctx.narrow.value = false
    ctx.leftOpen.value = true
    ctx.onSelectSession('session-2')
    await nextTick()
    expect(ctx.leftOpen.value).toBe(true)
  })
})

describe('模板接线证据', () => {
  it('关闭态 inert/aria-hidden/visibility 三件套与焦点引用', () => {
    expect(viewSource).toContain(':inert="drawerClosed"')
    expect(viewSource).toContain(':aria-hidden="drawerClosed ? \'true\' : null"')
    expect(viewSource).toContain('@keydown.esc.stop="onDrawerEsc"')
    expect(viewSource).toContain('@select="onSelectSession"')
    expect(viewSource).toContain('ref="leftToggleRef"')
    expect(viewSource).toContain('ref="leftColRef"')
    expect(viewSource).toContain('aria-expanded="leftOpen"')
    expect(viewSource).toMatch(/visibility:\s*hidden/)
  })

  it('会话行降级为 listitem，主区域是真正的 button（无嵌套 button role）', () => {
    expect(sessionSource).toContain('role="listitem"')
    expect(sessionSource).toContain('class="session-main"')
    expect(sessionSource).toMatch(/<button[^>]*class="session-main"/)
    expect(sessionSource).not.toMatch(/class="session-item"[\s\S]{0,200}role="button"/)
  })
})
