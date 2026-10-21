// MCP 服务器管理页的纯逻辑，便于 vitest 直接单测

// 编辑 MCP 服务器的保存 payload：envText 未被用户改动时不带 env 键——
// 后端 McpServerUpdate 用 exclude_unset，缺省即保留原值；
// 列表接口 env 已脱敏，若把占位 '{}'/解析结果直接 PUT 回去会静默清空真实环境变量。
// 用户改过（含清空为 {}）才带上 env，保证「改 / 清 / 不动」三种意图都可行。
export function buildServerUpdatePayload({ command, args, env, enabled }, { envTouched } = {}) {
  const payload = { command, args, enabled }
  if (envTouched) payload.env = env
  return payload
}

// 通用「JSON 对象」解析：非法 JSON 与合法但非对象（数组/标量/null）都抛中文提示。
// 数组/标量若放行，后端 pydantic 422 只会返回英文校验信息（env：Input should be
// a valid dictionary），用户看不懂；工具参数更会在结果面板渲染成 "[object Object]"。
export function parseJsonObject(text, label = 'JSON') {
  const t = (text || '').trim()
  if (!t) return {}
  let obj
  try {
    obj = JSON.parse(t)
  } catch {
    throw new Error(`${label}不是合法 JSON`)
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
    throw new Error(`${label}需为 JSON 对象，如：{"KEY":"value"}`)
  }
  return obj
}

// 环境变量文本框解析（label=环境变量）
export function parseEnvText(text) {
  return parseJsonObject(text, '环境变量')
}

// 参数行解析：支持双引号包裹含空格的参数（Windows 路径常见），
// 引号内 \" 与 \\ 为转义；未闭合引号取到行尾。无引号仍按空白拆分。
export function parseArgsText(text) {
  const src = (text || '').trim()
  if (!src) return []
  const out = []
  let i = 0
  while (i < src.length) {
    while (i < src.length && /\s/.test(src[i])) i += 1
    if (i >= src.length) break
    let token = ''
    const quote = src[i]
    if (quote === '"' || quote === "'") {
      i += 1
      while (i < src.length && src[i] !== quote) {
        if (src[i] === '\\' && i + 1 < src.length && (src[i + 1] === quote || src[i + 1] === '\\')) {
          i += 1
        }
        token += src[i]
        i += 1
      }
      i += 1 // 跳过收尾引号
    } else {
      while (i < src.length && !/\s/.test(src[i])) {
        token += src[i]
        i += 1
      }
    }
    out.push(token)
  }
  return out
}

// 编辑回显：含空白/引号的参数加双引号并转义，保证 parseArgsText 往返无损。
// 旧实现直接 join(' ')，编辑保存时会把 "D:\my docs" 静默拆成两个参数。
export function formatArgs(args) {
  return (args || [])
    .map((a) => {
      const s = String(a)
      if (/[\s"']/.test(s)) {
        return `"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`
      }
      return s
    })
    .join(' ')
}

// 接口错误文案：兼容 detail 为字符串 / pydantic 422 校验数组两种形态，
// 避免结果面板出现 "[object Object]"。
export function describeApiError(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    const field = (first?.loc || []).slice(1).join('.')
    return `${field ? field + '：' : ''}${first?.msg || '参数不合法'}`
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail)
  return error?.message || '请求失败'
}
