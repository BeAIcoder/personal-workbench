// AI 助手（会话归档/导出、聊天图片附件）的纯逻辑，便于 vitest 直接单测

// 允许上传的图片后缀（与后端上传接口约定一致）
export const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'webp', 'gif']

// 附件选择 accept：原有文本/表格类 + 图片
export const ASSISTANT_ACCEPT = [
  '.txt', '.md', '.csv', '.json', '.log', '.py', '.html', '.xml', '.yaml', '.yml', '.sql', '.xlsx', '.xls',
  ...IMAGE_EXTS.map((e) => '.' + e),
].join(',')

// 会话列表查询参数：archived 页签（false=进行中 / true=已归档）
export function sessionListParams(archived) {
  return { archived: archived ? 'true' : 'false' }
}

// 导出 Markdown 的完整 URL（window.open 直接触发下载）
export function sessionExportUrl(sessionId, apiBase = '/api') {
  const base = String(apiBase || '/api').replace(/\/+$/, '')
  return `${base}/assistant/sessions/${encodeURIComponent(sessionId)}/export`
}

// 按文件名后缀（或 MIME）判断是否为图片文件
export function isImageFile(file) {
  if (!file) return false
  if (String(file.type || '').startsWith('image/')) return true
  const name = String(file.name || '')
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : ''
  return IMAGE_EXTS.includes(ext)
}

// 上传响应 kind === 'image'（后端契约）或后缀命中，都算图片附件
export function isImageAttachment(att) {
  if (!att) return false
  if (att.kind === 'image') return true
  return isImageFile(att)
}

// 图片回显地址：优先上传响应的 url（绝对地址直接用，相对地址拼 API_BASE），
// 其次选中时的本地 objectURL 预览；后端只返回服务器文件系统 path 时无浏览器可访问地址
export function attachmentImageSrc(att, apiBase = '/api') {
  if (!att) return ''
  const url = att.url ? String(att.url) : ''
  if (url) return /^https?:\/\//i.test(url) ? url : joinUrl(apiBase, url)
  if (att.previewUrl) return String(att.previewUrl)
  // 兼容后端把可访问的 URL 路径放在 path 字段的情况（Windows 盘符路径不可用）
  const p = att.path ? String(att.path) : ''
  if (p.startsWith('/') && !/^\/[a-zA-Z]:/.test(p)) return joinUrl(apiBase, p)
  return ''
}

function joinUrl(base, path) {
  const b = String(base || '/api').replace(/\/+$/, '')
  if (!path) return b
  return path.startsWith('/') ? b + path : `${b}/${path}`
}
