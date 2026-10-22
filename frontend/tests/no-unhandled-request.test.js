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
//   4. 只剩 try/finally 无 catch、或压根没有 try 的候选按行报出（附原因）。
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

const SRC = fileURLToPath(new URL('../src', import.meta.url))

function sourceFiles(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
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
  walk(ast, null, (node, parent) => {
    parents.set(node, parent)
    if (node.type === 'AwaitExpression') awaits.push(node)
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
