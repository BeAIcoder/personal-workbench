/**
 * 银行流水预览的竞态守卫与企业日期转换。
 *
 * 预览是重活（最多 20MB xlsx），用户可能在解析途中换文件：
 * 每次请求持有递增序号 + 文件快照，响应回来时两者任一不符即丢弃。
 */
export function compactDateToRange(dateMin, dateMax) {
  const toIso = (value) => {
    const text = String(value ?? '').trim()
    if (!/^\d{8}$/.test(text)) return ''
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  }
  const start = toIso(dateMin)
  const end = toIso(dateMax)
  return start && end ? [start, end] : []
}

export function isPreviewCurrent(seq, currentSeq, snapshot, currentFile) {
  return seq === currentSeq && snapshot === currentFile
}
