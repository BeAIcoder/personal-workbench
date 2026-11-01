// 前端模块注册：扫描 src/modules/*/manifest.js（跳过 _ 开头目录），汇总模块路由与菜单
// 模块 manifest 约定：
//   export default {
//     name: 'finance-hello',            // 目录名（唯一）
//     title: '模块示例',
//     routes: [{ path, name, component, meta }],   // 路由统一 /finance/<name>/...，组件懒加载
//     menu: { group: '财务', subgroup: '税务填报', title, icon, path, order },  // 侧边栏菜单项
//   }   // subgroup 可选：组内二级折叠子分组名（如「税务填报」）；缺省则直挂在分组下
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

// 菜单分组聚合（供 AppLayout 渲染）：group → { name, icon, items, subgroups }
// 带 subgroup 的项收进组内二级折叠子分组（按首项出现序排列），否则直挂在分组下；
// 输入已按 order 升序（getModuleMenus），items/subgroups 均保持 order 序。
export function getModuleMenuGroups() {
  const groups = new Map()
  for (const m of getModuleMenus()) {
    const gname = m.group || '模块'
    if (!groups.has(gname)) {
      groups.set(gname, { name: gname, icon: m.icon || 'Folder', items: [], subgroups: [] })
    }
    const group = groups.get(gname)
    if (m.subgroup) {
      let sub = group.subgroups.find((s) => s.name === m.subgroup)
      if (!sub) {
        sub = { name: m.subgroup, items: [] }
        group.subgroups.push(sub)
      }
      sub.items.push(m)
    } else {
      group.items.push(m)
    }
  }
  return [...groups.values()]
}
