// 前端构建印章：把 src/、index.html、vite.config.js、package.json、
// package-lock.json 的内容哈希写进 dist/.build-stamp.json。
//
// 为什么用内容哈希而不是 mtime：ZIP 解包不保留文件时间戳（Python zipfile
// 即如此），分发包解包后所有文件时间戳趋同且按解包顺序排列，mtime 语义会把
// 「产物较新」误判成「源码较新」，导致收件人端每次启动都重建前端。内容哈希
// 只认字节，解包、拷贝、git  checkout 都不影响判定。
//
// 由 package.json 的 postbuild 钩子调用；needs-build.js 读取印章判断
// 「源码自上次构建以来是否变化」。
// 无第三方依赖；命令行无参运行，根目录固定为本脚本上级目录（frontend），
// 不接收外部路径参数（消除命令行参数→文件系统的穿越污点入口）。
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { pathToFileURL } from 'node:url'

export const STAMP_REL = 'dist/.build-stamp.json'
const SOURCE_DIRS = ['src']
const SOURCE_FILES = ['index.html', 'vite.config.js', 'package.json', 'package-lock.json']

// 目录遍历统一入口：拼接后必须仍位于 rootDir 内（防穿越边界校验）
function safeJoin(rootDir, ...segments) {
  const target = path.resolve(rootDir, ...segments)
  const rel = path.relative(rootDir, target)
  if (rel.startsWith('..') || path.isAbsolute(rel)) return null
  return target
}

// 递归收集目录内全部文件（相对路径，posix 风格）
function collectFiles(rootDir, dirRel, out) {
  const start = safeJoin(rootDir, dirRel)
  if (start === null) return out
  let entries
  try {
    entries = fs.readdirSync(start, { withFileTypes: true })
  } catch {
    return out
  }
  for (const entry of entries) {
    const relFromRoot = path.relative(rootDir, path.join(start, entry.name))
    const full = safeJoin(rootDir, relFromRoot)
    if (full === null) continue
    if (entry.isDirectory()) {
      collectFiles(rootDir, relFromRoot, out)
    } else {
      out.push(relFromRoot.split(path.sep).join('/'))
    }
  }
  return out
}

// 源码清单 = src/ 全部文件 + 四个构建相关文件（可选文件缺失时跳过）
export function sourceFiles(rootDir) {
  const files = []
  for (const dir of SOURCE_DIRS) {
    if (!fs.existsSync(path.join(rootDir, dir))) continue
    collectFiles(rootDir, dir, files)
  }
  for (const rel of SOURCE_FILES) {
    if (fs.existsSync(path.join(rootDir, rel))) files.push(rel)
  }
  return [...new Set(files)].sort()
}

export function sourcesHash(rootDir) {
  const hash = crypto.createHash('sha256')
  for (const rel of sourceFiles(rootDir)) {
    hash.update(rel)
    hash.update('\0')
    hash.update(fs.readFileSync(path.join(rootDir, rel)))
    hash.update('\0')
  }
  return hash.digest('hex')
}

export function stampPath(rootDir) {
  return path.join(rootDir, ...STAMP_REL.split('/'))
}

export function readStamp(rootDir) {
  try {
    const data = JSON.parse(fs.readFileSync(stampPath(rootDir), 'utf8'))
    if (data && typeof data.sources === 'string') return data
    return null
  } catch {
    return null
  }
}

export function writeStamp(rootDir) {
  const stamp = {
    algorithm: 'sha256',
    sources: sourcesHash(rootDir),
    builtAt: new Date().toISOString(),
  }
  const target = stampPath(rootDir)
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.writeFileSync(target, JSON.stringify(stamp, null, 2) + '\n')
  return stamp
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  // CLI 只支持无参运行：根目录固定为本脚本上级（frontend）
  const root = path.resolve(import.meta.dirname, '..')
  const stamp = writeStamp(root)
  console.log('STAMP_WRITTEN ' + stamp.sources.slice(0, 12))
}
