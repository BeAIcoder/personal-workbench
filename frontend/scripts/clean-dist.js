// 构建前清空 dist。
// 本机 D: 盘上 Node 24 的 fs.rmSync / fs.rmdirSync(recursive) 会静默失效
// （Vite 默认的 build.emptyOutDir 依赖 rmSync，实测无法清空，见 R-11），
// 因此改用 readdir + unlink + rmdir 手动递归删除。
import fs from 'node:fs'
import path from 'node:path'

const dist = path.resolve(import.meta.dirname, '..', 'dist')

function remove(target) {
  if (fs.statSync(target).isDirectory()) {
    for (const entry of fs.readdirSync(target)) {
      remove(path.join(target, entry))
    }
    fs.rmdirSync(target)
  } else {
    fs.unlinkSync(target)
  }
}

if (fs.existsSync(dist)) {
  remove(dist)
  console.log('[prebuild] 已清空 ' + dist)
}
