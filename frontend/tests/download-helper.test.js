// Task 7 / FE L-133：下载必须挂 DOM 后点击、延迟 revoke，且文件名显式落地。
// node 测试环境没有真实 DOM：注入假 document/URL 驱动 saveBlob 的真实执行顺序。
import { describe, expect, it, vi } from 'vitest'
import { REVOKE_DELAY_MS, saveBlob } from '../src/utils/download.js'

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
