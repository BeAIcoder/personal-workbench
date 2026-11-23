import { describe, expect, it } from 'vitest'
import {
  DOC_STATUS,
  buildSearchParams,
  buildUploadForm,
  canDownload,
  docIndexSummary,
  docOcrNote,
  formatCitation,
  indexStatusMeta,
  parseTags,
  statusMeta,
} from '../src/utils/knowledge'

describe('knowledge 工具函数', () => {
  it('状态标签有中文名与 Element 类型', () => {
    expect(statusMeta('effective')).toEqual({ label: '现行有效', type: 'success' })
    expect(statusMeta('superseded').label).toBe('已废止')
    expect(statusMeta('unknown').label).toBe('unknown')
    expect(Object.keys(DOC_STATUS)).toHaveLength(4)
    expect(indexStatusMeta('embedding').label).toBe('向量化中')
  })

  it('引用优先用后端给出的 citation，缺失时按字段拼', () => {
    expect(formatCitation({ citation: '《差旅制度》 · v2.0 · 第 7 页' })).toBe('《差旅制度》 · v2.0 · 第 7 页')
    expect(
      formatCitation({ title: '差旅制度', doc: { version: 'v1.0', heading: '第三章', page_no: 7 } }),
    ).toBe('《差旅制度》 · v1.0 · 第三章 · 第 7 页')
  })

  it('检索参数裁剪空白并补默认值', () => {
    expect(buildSearchParams({ query: '  住宿  ' })).toEqual({
      query: '住宿',
      top_k: 8,
      scope: 'all',
      category: '',
      include_inactive: false,
    })
  })

  it('标签解析支持中英文逗号、分号与数组', () => {
    expect(parseTags('差旅,报销；制度')).toEqual(['差旅', '报销', '制度'])
    expect(parseTags([' 差旅 ', '', '报销'])).toEqual(['差旅', '报销'])
    expect(parseTags('')).toEqual([])
  })

  it('索引摘要与下载条件', () => {
    expect(docIndexSummary({ chunk_count: 12, char_count: 6000 })).toBe('12 块 · 6000 字')
    expect(canDownload({ source_name: 'policy.docx' })).toBe(true)
    expect(canDownload({})).toBe(false)
    expect(docOcrNote({ ocr_pages: 3 })).toBe('含 OCR 3 页')
    expect(docOcrNote({})).toBe('')
  })

  it('上传表单只追加非空字段，标签合并为逗号串', () => {
    const form = buildUploadForm(null, {
      title: ' 差旅制度 ',
      category: '制度规范',
      version: '',
      tags: ['差旅', '报销'],
    })
    expect(form.get('title')).toBe('差旅制度')
    expect(form.get('category')).toBe('制度规范')
    expect(form.get('version')).toBeNull()
    expect(form.get('tags')).toBe('差旅,报销')
  })
})
