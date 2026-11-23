// 知识库页的纯逻辑：状态/引用格式化、检索参数、标签解析，便于 vitest 直接单测

// 打开「新建记忆」弹窗：必须重置 id 与表单——否则编辑过上一条后再点新建，
// 保存会带着旧 id 走 PUT，覆盖掉上一条记忆。
export function openCreateMem(memDialog, memForm) {
  memDialog.id = null
  memDialog.visible = true
  memForm.content = ''
  memForm.pinned = false
  if ('type' in memForm) memForm.type = ''
}

export const DOC_CATEGORIES = ['制度规范', '流程指引', '软件手册', '模板表单', '其他']

export const DOC_STATUS = {
  draft: { label: '草案', type: 'info' },
  effective: { label: '现行有效', type: 'success' },
  superseded: { label: '已废止', type: 'warning' },
  archived: { label: '已归档', type: 'info' },
}

export const INDEX_STATUS = {
  ok: { label: '已索引', type: 'success' },
  embedding: { label: '向量化中', type: 'warning' },
  embedding_failed: { label: '向量化失败', type: 'danger' },
  truncated: { label: '已截断', type: 'warning' },
  keyword_only: { label: '仅关键词', type: 'info' },
}

export function statusMeta(status) {
  return DOC_STATUS[status] || { label: status || '未知', type: 'info' }
}

export function indexStatusMeta(status) {
  return INDEX_STATUS[status] || { label: status || '未知', type: 'info' }
}

export function formatCitation(hit) {
  if (!hit) return ''
  if (hit.citation) return hit.citation
  const doc = hit.doc || {}
  const parts = [`《${hit.title || doc.title || '未命名'}》`]
  if (doc.version) parts.push(doc.version)
  if (doc.heading) parts.push(doc.heading)
  if (doc.page_no) parts.push(`第 ${doc.page_no} 页`)
  return parts.join(' · ')
}

export function buildSearchParams(form = {}) {
  return {
    query: String(form.query || '').trim(),
    top_k: Number(form.top_k || 8),
    scope: form.scope || 'all',
    category: form.category || '',
    include_inactive: Boolean(form.include_inactive),
  }
}

export function parseTags(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean)
  return String(value || '')
    .split(/[,，;；\n]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function docIndexSummary(doc = {}) {
  const chunks = Number(doc.chunk_count || 0)
  const chars = Number(doc.char_count || 0)
  const parts = [`${chunks} 块`]
  if (chars) parts.push(`${chars} 字`)
  return parts.join(' · ')
}

export function canDownload(doc = {}) {
  return Boolean(doc.source_name)
}

export function docOcrNote(doc = {}) {
  const pages = Number(doc.ocr_pages || 0)
  return pages > 0 ? `含 OCR ${pages} 页` : ''
}

// 上传表单 → FormData；只追加非空字段，空串交给后端默认值处理
export function buildUploadForm(file, form = {}) {
  const data = new FormData()
  if (file) data.append('file', file)
  const mapping = {
    title: 'title',
    category: 'category',
    department: 'department',
    doc_no: 'doc_no',
    version: 'version',
    status: 'status',
    effective_from: 'effective_from',
    effective_to: 'effective_to',
    owner: 'owner',
    summary: 'summary',
  }
  for (const [key, field] of Object.entries(mapping)) {
    const value = form[key]
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      data.append(field, String(value).trim())
    }
  }
  const tags = parseTags(form.tags)
  if (tags.length) data.append('tags', tags.join(','))
  return data
}
