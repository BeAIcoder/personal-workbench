/**
 * 浏览器下载统一出口：Blob → object URL → 挂 DOM → 点击 → 移出 → 延迟回收。
 *
 * 旧写法 `a.click(); URL.revokeObjectURL(url)` 在 Firefox/部分环境会因对象 URL 被
 * 同步回收而中断下载；`a` 未挂 DOM 时部分浏览器也不触发。这里统一处理，
 * 并允许测试注入 document/URL/定时器（node 环境无 DOM）。
 */
export const REVOKE_DELAY_MS = 10000

export function saveBlob(data, filename, options = {}) {
  const doc = options.document ?? globalThis.document
  const urlApi = options.url ?? globalThis.URL
  const schedule = options.setTimeout ?? globalThis.setTimeout
  if (!doc || typeof doc.createElement !== 'function' || !doc.body || !urlApi?.createObjectURL) {
    return false
  }
  const objectUrl = urlApi.createObjectURL(data)
  const link = doc.createElement('a')
  link.href = objectUrl
  link.download = filename || '下载文件'
  if (link.style) link.style.display = 'none'
  doc.body.appendChild(link)
  link.click()
  link.remove?.()
  schedule(() => urlApi.revokeObjectURL(objectUrl), options.revokeDelayMs ?? REVOKE_DELAY_MS)
  return true
}
