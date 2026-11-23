import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

// 拦截器（src/api/index.js）toast 之后仍会 Promise.reject，所以每个 await 请求都必须
// 自己兜底：要么 promise 自带 .catch()，要么被带 catch 的 try 包住（含跨嵌套函数/箭头
// 函数的词法边界）。漏了就是一次未捕获 rejection——main.js 的 window
// 'unhandledrejection' 会把它采集进 /api/debug/client-errors，把真实故障淹没；同时
// 成功路径（如刷新列表）也会被跳过，界面停在旧数据上。
//
// 检测口径（AST，替代旧的「恰好 2 格缩进」行正则——那个写法看不见 try/finally 无
// catch 的 4 格缩进语句，也管不了嵌套函数/箭头函数）：
//   1. .vue 取 <script setup>（没有则 <script>）内容，.js 取整文件，交给 @babel/parser；
//   2. 请求调用 = 调用链最左标识符为 api / *Api / fetch / axios，或 Promise.all/race/
//      allSettled 的参数里出现这类调用；
//   3. await 了请求调用即候选：await 的表达式本身是 .catch(...) → 有保护；否则沿父链
//      找词法包围它的 try 块（await 落在 catch/finally 子句里不算被包住），任一带
//      catch → 有保护；
//   4. 只剩 try/finally 无 catch、或压根没有 try 的候选按行报出（附原因）；
//   5. 不 await 的请求链同样是候选：`.then(...)` 丢弃结果（语句位置）且链尾没有
//      `.catch` → 报 'then 无 catch'；整条请求调用被当作语句丢弃（含 void 包裹）
//      且没有 .catch → 报 '未处理的请求调用'。赋给变量 / return 交给调用方的不算
//      （由调用方负责兜底），避免把 Promise 传递写法误判。
// 解析器取自 package-lock 已有的 @babel/parser 与 @vue/compiler-sfc（既有依赖，
// 未新增）；只覆盖本项目写法（标准 ES 模块 + script setup，无 TS），解析失败以
// parse-error 显式报出而不是静默跳过；模板内联 async 处理器本项目未使用，不在覆盖范围。
// 扫描整个 src 而非固定清单：新页面默认纳管，不再依赖"记得把文件加进列表"。

// 两个解析包都是 CJS 导出，用 createRequire 取，避免 ESM/CJS 互操作的不确定性
const require = createRequire(import.meta.url)
const parseSfc = require('@vue/compiler-sfc').parse
const parseJS = require('@babel/parser').parse

const REQUEST_ROOT = /^(?:api|[A-Za-z]*Api|fetch|axios)$/
const PROMISE_COMBINATORS = new Set(['all', 'race', 'allSettled'])
// 只有这些成员名才算「真的发出了 HTTP 请求」——api.interceptors.response.use(...)、
// api.defaults.baseURL 之类挂在同一个小写 api 根上的用法不算
const HTTP_VERBS = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'options'])

const SRC = fileURLToPath(new URL('../src', import.meta.url))

function sourceFiles(dir, out = []) {
  const root = path.resolve(dir)
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    // 防目录穿越：拼接后必须仍位于本目录内（basename 白名单语义）
    const full = path.resolve(root, entry.name)
    if (full !== root && !full.startsWith(root + path.sep)) continue
    if (entry.isDirectory()) sourceFiles(full, out)
    else if (/\.(vue|js)$/.test(entry.name)) out.push(full)
  }
  return out
}

// 调用链最左标识符：api.get → 'api'，taskApi.list → 'taskApi'，fetch → 'fetch'
function leftmostName(node) {
  let cur = node
  while (cur) {
    if (cur.type === 'Identifier') return cur.name
    if (cur.type === 'MemberExpression') {
      cur = cur.object
      continue
    }
    if (cur.type === 'CallExpression') {
      cur = cur.callee
      continue
    }
    return null
  }
  return null
}

function isRequestCall(node) {
  if (!node || node.type !== 'CallExpression') return false
  const { callee } = node
  if (callee.type === 'Identifier') return REQUEST_ROOT.test(callee.name)
  if (callee.type !== 'MemberExpression') return false
  const root = leftmostName(callee)
  if (REQUEST_ROOT.test(root || '')) return true
  // Promise.all/race/allSettled 包住请求调用：整体 reject 时同样没有兜底
  return root === 'Promise'
    && callee.property?.type === 'Identifier'
    && PROMISE_COMBINATORS.has(callee.property.name)
    && node.arguments.some((arg) => containsRequestCall(arg))
}

// 子表达式里是否出现请求调用（供 Promise.* 判断参数用）
function containsRequestCall(node) {
  if (!node || typeof node !== 'object') return false
  if (isRequestCall(node)) return true
  for (const key of Object.keys(node)) {
    if (key === 'loc' || key === 'start' || key === 'end') continue
    const value = node[key]
    if (Array.isArray(value)) {
      if (value.some((child) => containsRequestCall(child))) return true
    } else if (value && typeof value.type === 'string') {
      if (containsRequestCall(value)) return true
    }
  }
  return false
}

// await 的表达式本身是 .catch(...)：promise 自己接了 rejection
function hasOwnCatch(argument) {
  return argument.type === 'CallExpression'
    && argument.callee.type === 'MemberExpression'
    && argument.callee.property?.name === 'catch'
}

// 领域 API 根（knowledgeApi / assistantApi / taskApi…）：方法名是业务语义（docs/list/run），
// 不是 HTTP 动词，但同样是请求；只排除基础设施成员。
const DOMAIN_API_ROOT = /^[A-Za-z]+Api$/
const NON_REQUEST_MEMBERS = new Set(['interceptors', 'defaults'])

// 真的发出了 HTTP 请求的调用（排除 api.interceptors.* / api.defaults.* 等同根用法）
function isHttpRequestCall(node) {
  if (!node || node.type !== 'CallExpression') return false
  const { callee } = node
  if (callee.type === 'Identifier') return callee.name === 'fetch' || callee.name === 'axios'
  if (callee.type !== 'MemberExpression' || callee.property?.type !== 'Identifier') return false
  const root = leftmostName(callee)
  if (!root) return false
  if (root !== 'api' && DOMAIN_API_ROOT.test(root)) {
    // knowledgeApi.docs / assistantApi.sessions.list 等：任意业务方法都算请求
    return !NON_REQUEST_MEMBERS.has(callee.property.name)
  }
  return REQUEST_ROOT.test(root) && HTTP_VERBS.has(callee.property.name)
}

// 从最外层调用沿 callee/object 链取到根，得到整条调用链上的所有调用节点
function chainCalls(node) {
  const calls = []
  let cur = node
  while (cur && cur.type === 'CallExpression') {
    calls.push(cur)
    if (cur.callee.type === 'Identifier') break
    if (cur.callee.type !== 'MemberExpression') break
    cur = cur.callee.object
  }
  return calls
}

function chainHasMethod(node, name) {
  return chainCalls(node).some(
    (call) => call.callee.type === 'MemberExpression' && call.callee.property?.name === name,
  )
}

function chainHasHttpRequest(node) {
  return chainCalls(node).some(isHttpRequestCall)
}

// 整条链的结果被当作语句丢弃（赋给变量 / return / await 都不算；void 包裹算丢弃）
function isDiscardedStatement(node, parents) {
  let cur = node
  while (true) {
    const parent = parents.get(cur)
    if (!parent) return false
    if (parent.type === 'MemberExpression' && parent.object === cur) {
      cur = parent
      continue
    }
    if (parent.type === 'CallExpression' && parent.callee === cur) {
      cur = parent
      continue
    }
    if (parent.type === 'UnaryExpression' && parent.operator === 'void' && parent.argument === cur) {
      cur = parent
      continue
    }
    return parent.type === 'ExpressionStatement'
  }
}

// .then(onFulfilled, onRejected)：第二个参数就是 rejection 处理器（函数或标识符）
function hasThenRejectionHandler(node) {
  const handler = node.arguments?.[1]
  if (!handler) return false
  if (handler.type === 'NullLiteral') return false
  return handler.type === 'ArrowFunctionExpression'
    || handler.type === 'FunctionExpression'
    || handler.type === 'Identifier'
}

// 链上是否有更外层的 .catch：api.get().then(cb).catch(cb) 的 then 节点向上能找到
function hasCatchAfter(node, parents) {
  let cur = node
  while (true) {
    const parent = parents.get(cur)
    if (!parent) return false
    if (parent.type === 'MemberExpression' && parent.object === cur) {
      cur = parent
      continue
    }
    if (parent.type === 'CallExpression' && parent.callee === cur) {
      if (parent.callee.type === 'MemberExpression' && parent.callee.property?.name === 'catch') return true
      cur = parent
      continue
    }
    return false
  }
}

function walk(node, parent, visit) {
  visit(node, parent)
  for (const key of Object.keys(node)) {
    if (key === 'loc' || key === 'start' || key === 'end' || key === 'type') continue
    const value = node[key]
    if (Array.isArray(value)) {
      for (const child of value) if (child && typeof child.type === 'string') walk(child, node, visit)
    } else if (value && typeof value.type === 'string') {
      walk(value, node, visit)
    }
  }
}

function analyzeScript(code, baseLine) {
  const ast = parseJS(code, { sourceType: 'module' })
  const parents = new Map()
  const awaits = []
  const thens = []
  const statements = []
  walk(ast, null, (node, parent) => {
    parents.set(node, parent)
    if (node.type === 'AwaitExpression') awaits.push(node)
    if (node.type === 'ExpressionStatement') statements.push(node)
    if (
      node.type === 'CallExpression'
      && node.callee.type === 'MemberExpression'
      && node.callee.property?.name === 'then'
    ) thens.push(node)
  })
  const hits = []
  for (const node of awaits) {
    if (!isRequestCall(node.argument)) continue
    if (hasOwnCatch(node.argument)) continue
    let cur = node
    let sawTry = false
    let guarded = false
    while (cur) {
      const parent = parents.get(cur)
      if (!parent) break
      // 只有从 try 的 block 一路上来才算被包住；从 handler/finalizer 上来不算
      if (parent.type === 'TryStatement' && parent.block === cur) {
        sawTry = true
        if (parent.handler) guarded = true
      }
      cur = parent
    }
    if (guarded) continue
    hits.push({
      line: baseLine + node.loc.start.line - 1,
      reason: sawTry ? 'try/finally 无 catch' : '无 try/catch',
    })
  }

  // .then 链被丢弃且链尾没有 .catch：回调里抛错 / 请求失败都会变成未捕获 rejection
  for (const node of thens) {
    if (!chainHasHttpRequest(node.callee.object)) continue
    if (!isDiscardedStatement(node, parents)) continue
    if (hasCatchAfter(node, parents)) continue
    if (hasThenRejectionHandler(node)) continue
    hits.push({ line: baseLine + node.callee.property.loc.start.line - 1, reason: 'then 无 catch' })
  }

  // fire-and-forget：整条请求调用被当作语句丢弃，且没有 .catch（.then 由上一条规则管）
  for (const stmt of statements) {
    let expr = stmt.expression
    if (expr.type === 'ChainExpression') expr = expr.expression
    if (expr.type === 'AwaitExpression') continue
    while (expr.type === 'UnaryExpression' && expr.operator === 'void') expr = expr.argument
    if (expr.type !== 'CallExpression') continue
    if (chainHasMethod(expr, 'then') || chainHasMethod(expr, 'catch')) continue
    if (!chainHasHttpRequest(expr)) continue
    hits.push({ line: baseLine + expr.loc.start.line - 1, reason: '未处理的请求调用' })
  }
  return hits
}

function extractScript(source, filename) {
  if (!filename.endsWith('.vue')) return { code: source, baseLine: 1 }
  const { descriptor, errors } = parseSfc(source)
  if (errors.length) throw new Error(`SFC 解析失败：${errors[0].message}`)
  const block = descriptor.scriptSetup || descriptor.script
  if (!block) return null
  // content 从开标签所在行开始，AST 行号 + 标签行号 - 1 即原始文件行号
  return { code: block.content, baseLine: block.loc.start.line }
}

// 扫描单个源码（.vue 只扫 script 块），返回 [{ line, reason, snippet }]，行号为原始文件行号
export function scanUnguardedRequests(source, filename = '') {
  let script
  try {
    script = extractScript(source, filename)
  } catch (error) {
    return [{ line: 1, reason: `parse-error：${error.message}`, snippet: source.split('\n')[0].trim() }]
  }
  if (!script) return []
  let hits
  try {
    hits = analyzeScript(script.code, script.baseLine)
  } catch (error) {
    return [{ line: script.baseLine, reason: `parse-error：${error.message}`, snippet: '' }]
  }
  const lines = source.split('\n')
  return hits.map((hit) => ({ ...hit, snippet: (lines[hit.line - 1] || '').trim() }))
}

const FILES = sourceFiles(SRC).sort()

describe('全站请求调用不得留下未捕获 rejection', () => {
  it('扫描范围覆盖 src 下全部 .vue/.js 文件', () => {
    expect(FILES.length).toBeGreaterThanOrEqual(80)
  })

  it.each(FILES)('%s 的 await 请求都有 catch 保护', (full) => {
    const source = readFileSync(full, 'utf-8')
    const rel = path.relative(SRC, full)
    const hits = scanUnguardedRequests(source, rel)
    expect(hits, `未捕获的请求调用：${hits.map((h) => `${rel}:L${h.line} [${h.reason}] ${h.snippet}`).join(' | ')}`).toEqual([])
  })
})
