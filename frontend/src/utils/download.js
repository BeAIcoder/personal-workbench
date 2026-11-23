/**
 * 浏览器下载统一出口：Blob → object URL → 挂 DOM → 点击 → 移出 → 延迟回收。
 *
 * 旧写法 `a.click(); URL.revokeObjectURL(url)` 在 Firefox/部分环境会因对象 URL 被
 * 同步回收而中断下载；`a` 未挂 DOM 时部分浏览器也不触发。这里统一处理，
 * 并允许测试注入 document/URL/定时器（node 环境无 DOM）。
 */
export const REVOKE_DELAY_MS = 10000
// 打印时 PDF 的 object URL 要活得比下载久得多：阅读器按需分页读取，
// 沿用 10 秒回收会让页签在用户点打印前变空白，这里给 10 分钟兜底。
export const PRINT_REVOKE_DELAY_MS = 600000
// 新页签拿到 PDF 后再等一小会儿，让阅读器初始化出工具栏再尝试 focus/print
export const PRINT_DELAY_MS = 800

export function saveBlob(data, filename, options = {}) {
  const doc = options.document ?? globalThis.document
  const urlLib = options.url ?? globalThis.URL
  const schedule = options.setTimeout ?? globalThis.setTimeout
  if (!doc || typeof doc.createElement !== 'function' || !doc.body || !urlLib?.createObjectURL) {
    return false
  }
  const objectUrl = urlLib.createObjectURL(data)
  const link = doc.createElement('a')
  link.href = objectUrl
  link.download = filename || '下载文件'
  if (link.style) link.style.display = 'none'
  doc.body.appendChild(link)
  link.click()
  link.remove?.()
  schedule(() => urlLib.revokeObjectURL(objectUrl), options.revokeDelayMs ?? REVOKE_DELAY_MS)
  return true
}

/**
 * 在用户点击的同步阶段预开空白标签页。
 *
 * 浏览器只把「用户手势内」的 window.open 当成合法弹窗；等请求回来再 open 会被拦截。
 * 因此先拿到窗口句柄，失败（被拦截/环境无 window）返回 null，由调用方提示改用下载。
 */
export function openPrintWindow(options = {}) {
  const opener = options.open ?? globalThis.open
  if (typeof opener !== 'function') return null
  try {
    // 分离调用 window.open 在部分浏览器会抛 Illegal invocation，显式绑定全局对象
    return opener.call(globalThis, '', '_blank') || null
  } catch {
    return null
  }
}

/**
 * 把 PDF Blob 交给已预开的标签页：导航 → 延时 focus/print → 长延时回收。
 *
 * 不监听子窗口 unload：导航尚未提交时监听会落在空白文档上，导航提交反而提前回收 PDF。
 * 阅读器对自动打印的支持因浏览器而异，失败只是没弹打印对话框，PDF 仍在页签里可手动 Ctrl+P；
 * 这里任何一步失败都不抛异常，只在缺窗口/缺 URL API 时返回假值，让调用方关掉空白页。
 */
export function showPdfInWindow(data, options = {}) {
  const win = options.window ?? globalThis.window
  const urlLib = options.url ?? globalThis.URL
  const schedule = options.setTimeout ?? globalThis.setTimeout
  const cancelTimeout = options.clearTimeout ?? globalThis.clearTimeout
  if (!win || !urlLib?.createObjectURL || typeof schedule !== 'function') return false

  let objectUrl
  try {
    objectUrl = urlLib.createObjectURL(data)
  } catch {
    return false
  }
  if (!objectUrl) return false

  let revoked = false
  let fallbackTimer = null
  const revoke = () => {
    if (revoked) return
    revoked = true
    if (fallbackTimer !== null && typeof cancelTimeout === 'function') cancelTimeout(fallbackTimer)
    fallbackTimer = null
    urlLib.revokeObjectURL(objectUrl)
  }

  try {
    win.location.href = objectUrl
  } catch {
    revoke()
    return false
  }

  schedule(() => {
    try {
      win.focus?.()
    } catch {
      /* 阅读器不允许跨文档 focus：用户手动切换即可 */
    }
    try {
      win.print?.()
    } catch {
      /* 自动打印被策略拦下：保留阅读器内 Ctrl+P */
    }
  }, options.printDelayMs ?? PRINT_DELAY_MS)

  fallbackTimer = schedule(revoke, options.revokeDelayMs ?? PRINT_REVOKE_DELAY_MS)
  return objectUrl
}
