// 判断前端是否需要重新构建：比较 src、index.html、构建配置、
// package.json/package-lock.json 与 dist 产物的修改时间。
// dist 产物缺失或任一源更新时判定需要构建。
// 供 启动工作台.bat 调用：退出码 0 = 产物最新（跳过构建），1 = 需要构建。
// 无第三方依赖；命令行可传入前端根目录（默认取本脚本上级目录）。
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const DIST_ENTRY = 'dist/index.html'
const SOURCE_DIRS = ['src']
const SOURCE_FILES = ['index.html', 'vite.config.js', 'package.json', 'package-lock.json']

// 递归取目录内最新文件（相对路径 + mtimeMs）；目录本身 mtime 不可靠，必须走文件。
function newestFile(rootDir, dirRel) {
  let newest = null
  const walk = (current) => {
    let entries
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name)
      let stat
      try {
        stat = fs.statSync(full)
      } catch {
        continue
      }
      if (stat.isDirectory()) {
        walk(full)
      } else if (newest === null || stat.mtimeMs > newest.mtimeMs) {
        newest = { rel: path.relative(rootDir, full), mtimeMs: stat.mtimeMs }
      }
    }
  }
  walk(path.join(rootDir, dirRel))
  return newest
}

export function checkNeedsBuild(rootDir) {
  const reasons = []
  let artifactMtimeMs = null
  try {
    artifactMtimeMs = fs.statSync(path.join(rootDir, DIST_ENTRY)).mtimeMs
  } catch {
    reasons.push('dist/index.html missing')
  }

  const sources = []
  for (const rel of SOURCE_DIRS) {
    if (!fs.existsSync(path.join(rootDir, rel))) {
      reasons.push(rel + '/ missing')
      continue
    }
    const newest = newestFile(rootDir, rel)
    if (newest) sources.push(newest)
  }
  for (const rel of SOURCE_FILES) {
    try {
      sources.push({ rel, mtimeMs: fs.statSync(path.join(rootDir, rel)).mtimeMs })
    } catch {
      // 可选文件缺失时跳过
    }
  }

  const stale =
    artifactMtimeMs === null ? [] : sources.filter((s) => s.mtimeMs > artifactMtimeMs).map((s) => 'newer: ' + s.rel)

  return {
    needsBuild: reasons.length > 0 || stale.length > 0,
    reasons: [...reasons, ...stale],
    artifactMtimeMs,
    sources,
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const root = process.argv[2] ? path.resolve(process.argv[2]) : path.resolve(import.meta.dirname, '..')
  const result = checkNeedsBuild(root)
  if (result.needsBuild) {
    console.log('NEEDS_BUILD')
    for (const reason of result.reasons) console.log('# ' + reason)
    process.exitCode = 1
  } else {
    console.log('UP_TO_DATE')
    process.exitCode = 0
  }
}
