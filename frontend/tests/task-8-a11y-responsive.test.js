// Task 8 / FE L-127、L-131：主要列表/会话/日程/模型的可点击非按钮元素必须键盘可达；
// 1280/1024 窄屏收紧侧栏与搜索，助手左列在窄屏变为可操作抽屉，不做全站视觉重设计。
import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'

const { isActivationKey, activationKeydown } = await import('../src/utils/a11y.js')

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const sessionSource = read('../src/components/assistant/SessionList.vue')
const scheduleSource = read('../src/views/ScheduleView.vue')
const settingsSource = read('../src/components/assistant/SettingsDrawer.vue')
const layoutSource = read('../src/components/AppLayout.vue')
const searchSource = read('../src/components/GlobalSearch.vue')
const assistantSource = read('../src/views/AssistantView.vue')

describe('键盘激活纯逻辑', () => {
  it('Enter / Space / Spacebar 视为激活键', () => {
    expect(isActivationKey({ key: 'Enter' })).toBe(true)
    expect(isActivationKey({ key: ' ' })).toBe(true)
    expect(isActivationKey({ key: 'Spacebar' })).toBe(true)
    expect(isActivationKey({ key: 'Escape' })).toBe(false)
    expect(isActivationKey(null)).toBe(false)
  })

  it('激活时阻止默认滚动并调用处理器，其它键不触发', () => {
    const fn = vi.fn()
    const handler = activationKeydown(fn)
    const space = { key: ' ', preventDefault: vi.fn() }
    handler(space)
    expect(space.preventDefault).toHaveBeenCalled()
    expect(fn).toHaveBeenCalledTimes(1)
    handler({ key: 'a', preventDefault: vi.fn() })
    expect(fn).toHaveBeenCalledTimes(1)
  })
})

describe('会话 / 日程 / 模型键盘可达（L-131）', () => {
  it('会话行有 listitem 语义，主区域是主按钮，操作图标有标签', () => {
    expect(sessionSource).toMatch(/class="session-item"[\s\S]*?role="listitem"/)
    expect(sessionSource).toMatch(/<button[^>]*class="session-main"[^>]*aria-label="`打开会话 \$\{s.title\}`"/)
    expect(sessionSource).not.toMatch(/class="session-item"[\s\S]{0,250}@keydown\.enter/)
    expect(sessionSource).toContain(':aria-label="`归档会话 ${s.title}`"')
    expect(sessionSource).toContain(':aria-label="`删除会话 ${s.title}`"')
  })

  it('主按钮内只允许 span，不允许 div；操作图标常显并保留 focus-within 规则', () => {
    const main = sessionSource.match(/<button[^>]*class="session-main"[^>]*>([\s\S]*?)<\/button>/)?.[1] || ''
    expect(main).toContain('<span class="session-title"')
    expect(main).toContain('<span class="session-meta"')
    expect(main).not.toContain('<div')
    expect(sessionSource).toMatch(/\.session-actions\s*\{[^}]*display:\s*flex/)
    expect(sessionSource).toMatch(/\.session-item:focus-within\s+\.session-actions\s*\{[^}]*display:\s*flex/)
  })

  it('日程行使用 listitem + 主按钮，不再用 role=button 包裹子按钮', () => {
    expect(scheduleSource).toMatch(/class="event-item"[^>]*role="listitem"/)
    expect(scheduleSource).not.toMatch(/class="event-item"[^>]*role="button"/)
    expect(scheduleSource).toMatch(/<button[^>]*class="event-main"[^>]*aria-label="`编辑日程 \$\{ev.title\}`"/)
    expect(scheduleSource).not.toContain('@keydown="onEventKey($event, ev)"')
    const main = scheduleSource.match(/<button[^>]*class="event-main"[^>]*>([\s\S]*?)<\/button>/)?.[1] || ''
    expect(main).toContain('<span class="event-title"')
    expect(main).toContain('<span class="event-meta"')
    expect(main).not.toContain('<div')
  })

  it('设置页模型名使用统一的键盘激活助手并有读屏标签', () => {
    expect(settingsSource).toMatch(/class="model-name"[^>]*role="button"/)
    expect(settingsSource).toMatch(/class="model-name"[^>]*tabindex="0"/)
    expect(settingsSource).toContain(':aria-label="`启用模型 ${m.model}`"')
    expect(settingsSource).toContain('@keydown="onModelKeydown($event, m)"')
    expect(settingsSource).toContain('activationKeydown')
  })
})

describe('1280 / 1024 窄屏（L-127）', () => {
  it('侧栏宽度不再写死 220px，1280/1024 两级收紧', () => {
    expect(layoutSource).not.toContain('<el-aside width="220px"')
    expect(layoutSource).toContain('@media (max-width: 1280px)')
    expect(layoutSource).toContain('@media (max-width: 1024px)')
  })

  it('全局搜索宽度自适应并有两级断点', () => {
    expect(searchSource).not.toContain('style="width: 340px"')
    expect(searchSource).toContain('class="global-search"')
    expect(searchSource).toContain('@media (max-width: 1280px)')
    expect(searchSource).toContain('@media (max-width: 1024px)')
  })

  it('助手左列在窄屏可折叠为抽屉，按钮暴露 aria-expanded', () => {
    expect(assistantSource).toContain('class="left-toggle"')
    expect(assistantSource).toContain(':aria-expanded="leftOpen"')
    expect(assistantSource).toContain('aria-controls="assistant-left"')
    expect(assistantSource).toContain('@media (max-width: 1100px)')
    expect(assistantSource).toContain('class="left-col')
  })

  it('设置抽屉宽度在窄屏不超出视口', () => {
    expect(settingsSource).toContain('size="min(640px, 92vw)"')
  })
})
