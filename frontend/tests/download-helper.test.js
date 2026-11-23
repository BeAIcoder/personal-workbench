// Task 7 / FE L-133：下载必须挂 DOM 后点击、延迟 revoke，且文件名显式落地。
// node 测试环境没有真实 DOM：注入假 document/URL 驱动 saveBlob 的真实执行顺序。
import { describe, expect, it, vi } from 'vitest'
import {
  PRINT_DELAY_MS,
  PRINT_REVOKE_DELAY_MS,
  REVOKE_DELAY_MS,
  openPrintWindow,
  saveBlob,
  showPdfInWindow,
} from '../src/utils/download.js'

function fakeDom() {
  const events = []
  const link = {
    href: '',
    download: '',
    style: {},
    click: () => events.push('click'),
    remove: () => events.push('remove'),
  }
  const body = { appendChild: (node) => { events.push('append'); node.parent = body } }
  const doc = {
    body,
    createElement: (tag) => { events.push(`create:${tag}`); return link },
  }
  const url = {
    createObjectURL: vi.fn(() => 'blob:mock-1'),
    revokeObjectURL: vi.fn(() => events.push('revoke')),
  }
  return { doc, url, link, events }
}

describe('saveBlob', () => {
  it('挂到 body 后再点击，随后移出 DOM', () => {
    const { doc, url, link, events } = fakeDom()
    const ok = saveBlob(new Blob(['x']), '发票汇总2026年10月.zip', { document: doc, url })
    expect(ok).toBe(true)
    expect(events).toEqual(['create:a', 'append', 'click', 'remove'])
    expect(link.download).toBe('发票汇总2026年10月.zip')
    expect(link.href).toBe('blob:mock-1')
    expect(url.revokeObjectURL).not.toHaveBeenCalled()
  })

  it('延迟回收 object URL（默认 10s），不打断浏览器下载', () => {
    const { doc, url } = fakeDom()
    const timers = []
    saveBlob(new Blob(['x']), 'a.xlsx', {
      document: doc,
      url,
      setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length },
    })
    expect(timers).toHaveLength(1)
    expect(timers[0].delay).toBe(REVOKE_DELAY_MS)
    expect(REVOKE_DELAY_MS).toBeGreaterThanOrEqual(10000)
    expect(url.revokeObjectURL).not.toHaveBeenCalled()
    timers[0].fn()
    expect(url.revokeObjectURL).toHaveBeenCalledWith('blob:mock-1')
  })

  it('缺文件名时给可恢复的默认名', () => {
    const { doc, url, link } = fakeDom()
    saveBlob(new Blob(['x']), '', { document: doc, url, setTimeout: () => 0 })
    expect(link.download).toBe('下载文件')
  })

  it('无 DOM（node 直接调用）时安全返回 false，不抛异常', () => {
    expect(saveBlob(new Blob(['x']), 'a.csv', { document: null, url: null })).toBe(false)
  })
})

// 打印兜底：必须在点击的同步阶段预开标签页，成功后才把 object URL 导航进去；
// PDF 的 object URL 不能沿用下载的 10 秒回收（阅读器还在读就被清空 → 空白页）。
function fakePrintWindow() {
  const calls = []
  const listeners = {}
  const win = {
    location: { href: '' },
    focus: () => calls.push('focus'),
    print: () => calls.push('print'),
    close: () => calls.push('close'),
    addEventListener: (name, handler) => {
      listeners[name] = handler
      calls.push(`on:${name}`)
    },
  }
  return { win, calls, listeners }
}

function fakeUrl() {
  return {
    createObjectURL: vi.fn(() => 'blob:pdf-1'),
    revokeObjectURL: vi.fn(),
  }
}

describe('openPrintWindow', () => {
  it('用 window.open(\'\', \'_blank\') 预开空白页并返回窗口句柄', () => {
    const { win } = fakePrintWindow()
    const open = vi.fn(() => win)
    expect(openPrintWindow({ open })).toBe(win)
    expect(open).toHaveBeenCalledWith('', '_blank')
  })

  it('被拦截（返回 null/空/抛错）时返回 null，不抛异常', () => {
    expect(openPrintWindow({ open: () => null })).toBeNull()
    expect(openPrintWindow({ open: () => undefined })).toBeNull()
    expect(
      openPrintWindow({
        open: () => {
          throw new Error('blocked by popup blocker')
        },
      }),
    ).toBeNull()
    expect(openPrintWindow({ open: undefined })).toBeNull()
  })
})

describe('showPdfInWindow', () => {
  it('导航到 PDF 的 object URL，延时 focus+print，且不在 10 秒内提前回收', () => {
    const { win, calls } = fakePrintWindow()
    const url = fakeUrl()
    const timers = []
    const objectUrl = showPdfInWindow(new Blob(['pdf']), {
      window: win,
      url,
      setTimeout: (fn, delay) => {
        timers.push({ fn, delay })
        return timers.length
      },
      clearTimeout: () => {},
    })

    expect(objectUrl).toBe('blob:pdf-1')
    expect(win.location.href).toBe('blob:pdf-1')
    // 同步阶段绝不能回收：阅读器还没读完
    expect(url.revokeObjectURL).not.toHaveBeenCalled()

    const printTimer = timers.find((t) => t.delay === PRINT_DELAY_MS)
    expect(printTimer).toBeTruthy()
    printTimer.fn()
    expect(calls).toEqual(['focus', 'print'])

    const fallback = timers.find((t) => t.delay === PRINT_REVOKE_DELAY_MS)
    expect(fallback).toBeTruthy()
    expect(PRINT_REVOKE_DELAY_MS).toBeGreaterThan(REVOKE_DELAY_MS * 10)
    fallback.fn()
    expect(url.revokeObjectURL).toHaveBeenCalledWith('blob:pdf-1')
  })

  it('不在导航期注册 unload 回收，只保留长延时兜底', () => {
    const { win, listeners, calls } = fakePrintWindow()
    const url = fakeUrl()
    const timers = []
    showPdfInWindow(new Blob(['pdf']), {
      window: win,
      url,
      setTimeout: (fn, delay) => {
        timers.push({ fn, delay })
        return timers.length
      },
      clearTimeout: () => {},
    })
    timers.find((t) => t.delay === PRINT_DELAY_MS).fn()

    expect(calls).toEqual(['focus', 'print'])
    expect(listeners.unload).toBeUndefined()
    expect(url.revokeObjectURL).not.toHaveBeenCalled()
    const fallback = timers.find((t) => t.delay === PRINT_REVOKE_DELAY_MS)
    fallback.fn()
    fallback.fn()
    expect(url.revokeObjectURL).toHaveBeenCalledTimes(1)
  })

  it('阅读器不支持 focus/print 时仍完成导航，不抛异常', () => {
    const url = fakeUrl()
    const win = { location: { href: '' } }
    const timers = []
    showPdfInWindow(new Blob(['pdf']), {
      window: win,
      url,
      setTimeout: (fn, delay) => {
        timers.push({ fn, delay })
        return timers.length
      },
      clearTimeout: () => {},
    })
    expect(win.location.href).toBe('blob:pdf-1')
    expect(() => timers.find((t) => t.delay === PRINT_DELAY_MS).fn()).not.toThrow()
  })

  it('缺窗口或 URL API 时返回假值，不创建 object URL', () => {
    const url = fakeUrl()
    expect(showPdfInWindow(new Blob(['x']), { window: null, url })).toBeFalsy()
    expect(url.createObjectURL).not.toHaveBeenCalled()
    const { win } = fakePrintWindow()
    // 与 saveBlob 一致：选项里的 null 视为「未提供」回落到全局，只有真的不可用才失败
    expect(showPdfInWindow(new Blob(['x']), { window: win, url: {} })).toBeFalsy()
  })
})
