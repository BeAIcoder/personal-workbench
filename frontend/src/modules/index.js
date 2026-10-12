// 前端模块注册：扫描 src/modules/*/manifest.js（跳过 _ 开头目录），汇总模块路由与菜单
// 模块 manifest 约定：
//   export default {
//     name: 'finance-hello',            // 目录名（唯一）
//     title: '模块示例',
//     routes: [{ path, name, component, meta }],   // 路由统一 /finance/<name>/...，组件懒加载
//     menu: { group: '财务', title, icon, path, order },  // 侧边栏菜单项
//   }
const files = import.meta.glob('./*/manifest.js', { eager: true })

const manifests = []

for (const [path, mod] of Object.entries(files)) {
  const dir = path.split('/')[1]
  if (dir.startsWith('_')) continue
  const manifest = mod.default
  if (!manifest || !manifest.name) continue
  manifests.push(manifest)
}

// 全部模块路由（追加到静态路由之后、404 兜底之前）
export function getModuleRoutes() {
  return manifests.flatMap((m) => m.routes || [])
}

// 全部模块菜单项（按 order 升序）
export function getModuleMenus() {
  return manifests
    .filter((m) => m.menu && m.menu.path)
    .map((m) => m.menu)
    .sort((a, b) => (a.order ?? 999) - (b.order ?? 999))
}
