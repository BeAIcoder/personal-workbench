import { describe, expect, it, vi } from 'vitest'
import { installChunkRecovery } from '../src/utils/chunkRecovery'

const RELOAD_KEY = 'personal-workbench:vite-preload-reload'
const PAGE_URL = 'http://127.0.0.1:8000/#/tasks'

function makeHarness({ getError = false, setError = false, removeError = false } = {}) {
  const listeners = new Map()
  const stored = new Map()
  const navigationCallbacks = []
  const sessionStorage = {
    getItem: vi.fn((key) => {
      if (getError) throw new Error('storage read blocked')
      return stored.get(key) ?? null
    }),
    setItem: vi.fn((key, value) => {
      if (setError) throw new Error('storage write blocked')
      stored.set(key, value)
    }),
    removeItem: vi.fn((key) => {
      if (removeError) throw new Error('storage remove blocked')
      stored.delete(key)
    }),
  }
  const win = {
    location: { href: PAGE_URL, reload: vi.fn() },
    sessionStorage,
    addEventListener: vi.fn((name, callback) => listeners.set(name, callback)),
  }
  const router = { afterEach: vi.fn((callback) => navigationCallbacks.push(callback)) }
  installChunkRecovery(win, router)

  return {
    win,
    stored,
    sessionStorage,
    emit() {
      const event = { preventDefault: vi.fn() }
      listeners.get('vite:preloadError')(event)
      return event
    },
    navigate(failure) {
      for (const callback of navigationCallbacks) callback({}, {}, failure)
    },
  }
}

describe('构建后懒加载模块恢复', () => {
  it('首次模块加载失败时记录当前页面并刷新一次', () => {
    const harness = makeHarness()

    const event = harness.emit()

    expect(event.preventDefault).toHaveBeenCalledTimes(1)
    expect(harness.sessionStorage.setItem).toHaveBeenCalledWith(RELOAD_KEY, PAGE_URL)
    expect(harness.win.location.reload).toHaveBeenCalledTimes(1)
  })

  it('同一页面再次失败时不重复刷新并保留原始错误', () => {
    const harness = makeHarness()
    harness.emit()

    const secondEvent = harness.emit()

    expect(harness.win.location.reload).toHaveBeenCalledTimes(1)
    expect(secondEvent.preventDefault).not.toHaveBeenCalled()
  })

  it('成功导航后清除防循环标记以允许下次版本更新恢复', () => {
    const harness = makeHarness()
    harness.emit()
    expect(harness.stored.has(RELOAD_KEY)).toBe(true)

    harness.navigate()
    expect(harness.stored.has(RELOAD_KEY)).toBe(false)

    harness.emit()
    expect(harness.win.location.reload).toHaveBeenCalledTimes(2)
  })

  it('导航失败时保留防循环标记并阻止连续刷新', () => {
    const harness = makeHarness()
    harness.emit()

    harness.navigate(new Error('任务模块仍然缺失'))

    expect(harness.stored.has(RELOAD_KEY)).toBe(true)
    const event = harness.emit()
    expect(harness.win.location.reload).toHaveBeenCalledTimes(1)
    expect(event.preventDefault).not.toHaveBeenCalled()
  })

  it('读取会话存储失败时不刷新也不吞掉错误', () => {
    const harness = makeHarness({ getError: true })

    const event = harness.emit()

    expect(harness.win.location.reload).not.toHaveBeenCalled()
    expect(event.preventDefault).not.toHaveBeenCalled()
  })

  it('写入会话存储失败时不刷新也不吞掉错误', () => {
    const harness = makeHarness({ setError: true })

    const event = harness.emit()

    expect(harness.win.location.reload).not.toHaveBeenCalled()
    expect(event.preventDefault).not.toHaveBeenCalled()
  })

  it('清理会话存储失败不影响成功导航', () => {
    const harness = makeHarness({ removeError: true })
    harness.emit()

    expect(() => harness.navigate()).not.toThrow()
  })
})
