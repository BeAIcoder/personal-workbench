import { describe, expect, it } from 'vitest'
import { encodePackFilePath, encodePackName } from '../src/utils/skillPack'

// 回归：技能包目录名/文件名直接拼进 URL 会被浏览器按 fragment/query 截断
//（tax#1 的启停请求实际打到 /skills/packs/tax/enabled，405/404）。
describe('技能包目录名 URL 编码', () => {
  it('编码 # ? % 与空格，避免 URL 被截断', () => {
    expect(encodePackName('tax#1')).toBe('tax%231')
    expect(encodePackName('a?b')).toBe('a%3Fb')
    expect(encodePackName('100%')).toBe('100%25')
    expect(encodePackName('含 空格')).toBe('%E5%90%AB%20%E7%A9%BA%E6%A0%BC')
  })

  it('普通目录名保持不变，服务端解码后与原值一致', () => {
    expect(encodePackName('invoice-review')).toBe('invoice-review')
    for (const name of ['tax#1', 'a?b', '含 空格', '<img>']) {
      expect(decodeURIComponent(encodePackName(name))).toBe(name)
    }
  })

  it('空值与 null 安全', () => {
    expect(encodePackName('')).toBe('')
    expect(encodePackName(null)).toBe('')
    expect(encodePackName(undefined)).toBe('')
  })
})

describe('技能包内文件路径编码', () => {
  it('逐段编码并保留 / 分隔', () => {
    expect(encodePackFilePath('SKILL.md')).toBe('SKILL.md')
    expect(encodePackFilePath('notes/check#1.md')).toBe('notes/check%231.md')
    expect(encodePackFilePath('参考/清单 1.md')).toBe(
      '%E5%8F%82%E8%80%83/%E6%B8%85%E5%8D%95%201.md',
    )
  })

  it('保留 / 分段与相对路径结构（穿越由后端路径校验拦截）', () => {
    expect(encodePackFilePath('../secret.md')).toBe('../secret.md')
  })

  it('空值安全', () => {
    expect(encodePackFilePath('')).toBe('')
    expect(encodePackFilePath(null)).toBe('')
  })
})
