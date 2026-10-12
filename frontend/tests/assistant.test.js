import { describe, expect, it } from 'vitest'
import {
  ASSISTANT_ACCEPT,
  IMAGE_EXTS,
  attachmentImageSrc,
  isImageAttachment,
  isImageFile,
  sessionExportUrl,
  sessionListParams,
} from '../src/utils/assistant'

describe('会话列表归档过滤参数', () => {
  it('进行中 → archived=false，已归档 → archived=true', () => {
    expect(sessionListParams(false)).toEqual({ archived: 'false' })
    expect(sessionListParams(true)).toEqual({ archived: 'true' })
  })

  it('缺省按进行中处理', () => {
    expect(sessionListParams()).toEqual({ archived: 'false' })
    expect(sessionListParams(undefined)).toEqual({ archived: 'false' })
  })
})

describe('会话导出 URL', () => {
  it('拼接 API_BASE 与导出路径', () => {
    expect(sessionExportUrl('s_abc123', '/api')).toBe('/api/assistant/sessions/s_abc123/export')
  })

  it('baseURL 尾部斜杠不重复；缺省 /api', () => {
    expect(sessionExportUrl('s_1', '/api/')).toBe('/api/assistant/sessions/s_1/export')
    expect(sessionExportUrl('s_1')).toBe('/api/assistant/sessions/s_1/export')
    expect(sessionExportUrl('s_1', 'http://127.0.0.1:8000/api')).toBe('http://127.0.0.1:8000/api/assistant/sessions/s_1/export')
  })

  it('session id 特殊字符转义', () => {
    expect(sessionExportUrl('a b/c', '/api')).toBe('/api/assistant/sessions/a%20b%2Fc/export')
  })
})

describe('图片类型判断', () => {
  it('后缀命中（大小写不敏感）', () => {
    expect(isImageFile({ name: 'a.png' })).toBe(true)
    expect(isImageFile({ name: 'a.JPG' })).toBe(true)
    expect(isImageFile({ name: 'a.webp' })).toBe(true)
    expect(isImageFile({ name: 'a.gif' })).toBe(true)
    expect(isImageFile({ name: 'a.txt' })).toBe(false)
    expect(isImageFile({ name: 'noext' })).toBe(false)
  })

  it('MIME 命中', () => {
    expect(isImageFile({ name: 'blob', type: 'image/png' })).toBe(true)
    expect(isImageFile({ name: 'a.csv', type: 'text/csv' })).toBe(false)
  })

  it('空值安全', () => {
    expect(isImageFile(null)).toBe(false)
    expect(isImageFile(undefined)).toBe(false)
    expect(isImageFile({})).toBe(false)
  })

  it('上传响应 kind=image 即图片（后端契约）', () => {
    expect(isImageAttachment({ kind: 'image', name: 'x.bin' })).toBe(true)
    expect(isImageAttachment({ kind: 'file', name: 'x.png' })).toBe(true)
    expect(isImageAttachment({ kind: 'csv_converted', name: 'x.csv' })).toBe(false)
    expect(isImageAttachment({ kind: 'file', name: 'x.xlsx' })).toBe(false)
    expect(isImageAttachment(null)).toBe(false)
  })
})

describe('图片回显地址', () => {
  it('上传响应 url：绝对地址直接用，相对地址拼 API_BASE', () => {
    expect(attachmentImageSrc({ url: 'https://cdn.example.com/a.png' }, '/api')).toBe('https://cdn.example.com/a.png')
    expect(attachmentImageSrc({ url: '/assistant/uploads/s_1/a.png' }, '/api')).toBe('/api/assistant/uploads/s_1/a.png')
  })

  it('无 url 时用本地 objectURL 预览', () => {
    expect(attachmentImageSrc({ previewUrl: 'blob:http://x/abc' }, '/api')).toBe('blob:http://x/abc')
  })

  it('url 优先于本地预览', () => {
    expect(attachmentImageSrc({ url: '/a.png', previewUrl: 'blob:xxx' }, '/api')).toBe('/api/a.png')
  })

  it('后端只给服务器文件系统 path（Windows 盘符）时无浏览器可访问地址', () => {
    expect(attachmentImageSrc({ path: 'D:\\data\\uploads\\s_1\\a.png' }, '/api')).toBe('')
    expect(attachmentImageSrc({ path: '/data/uploads/s_1/a.png' }, '/api')).toBe('/api/data/uploads/s_1/a.png')
    expect(attachmentImageSrc({}, '/api')).toBe('')
    expect(attachmentImageSrc(null, '/api')).toBe('')
  })
})

describe('附件 accept 清单', () => {
  it('在原有文本/表格类之外包含全部图片后缀', () => {
    for (const ext of ['.txt', '.md', '.csv', '.json', '.py', '.sql', '.xlsx', '.xls']) {
      expect(ASSISTANT_ACCEPT).toContain(ext)
    }
    for (const ext of IMAGE_EXTS) {
      expect(ASSISTANT_ACCEPT).toContain('.' + ext)
    }
    expect(IMAGE_EXTS).toEqual(['png', 'jpg', 'jpeg', 'webp', 'gif'])
  })
})
