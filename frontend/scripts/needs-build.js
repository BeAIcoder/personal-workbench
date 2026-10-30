// 判断前端是否需要重新构建：比较 src、index.html、构建配置、
// package.json/package-lock.json 的内容哈希与 dist 内的构建印章
// （dist/.build-stamp.json，由 postbuild 写入）。
// 不用 mtime 比较——ZIP 解包不保留文件时间戳（Python zipfile 即如此），
// 分发包解包后所有文件时间戳趋同，mtime 语义会误判「源码较新」。
// 供 启动工作台.bat 调用：退出码 0 = 产物最新（跳过构建），1 = 需要构建。
// 无第三方依赖；命令行无参运行，根目录固定为本脚本上级目录（frontend），
// 不接收外部路径参数（消除命令行参数→文件系统的穿越污点入口）。
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { readStamp, sourcesHash, STAMP_REL } from './write-build-stamp.js'

const DIST_ENTRY = 'dist/index.html'

export function checkNeedsBuild(rootDir) {
  const reasons = []
  if (!fs.existsSync(path.join(rootDir, DIST_ENTRY))) {
    reasons.push('dist/index.html missing')
  }
  const stamp = readStamp(rootDir)
  if (!stamp) {
    reasons.push('build stamp missing (' + STAMP_REL + ')')
  } else if (stamp.sources !== sourcesHash(rootDir)) {
    reasons.push('sources changed since last build')
  }
  return {
    needsBuild: reasons.length > 0,
    reasons,
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  // CLI 只支持无参运行：根目录固定为本脚本上级（frontend），
  // 不接收外部路径参数（消除命令行参数→文件系统的穿越污点入口）。
  const root = path.resolve(import.meta.dirname, '..')
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
