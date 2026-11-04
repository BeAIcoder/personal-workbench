// 前端模块注册：扫描 src/modules/*/manifest.js（跳过 _ 开头目录），汇总模块路由与菜单
// 模块 manifest 约定：
//   export default {
//     name: 'finance-hello',            // 目录名（唯一）
//     title: '模块示例',
//     routes: [{ path, name, component, meta }],   // 路由统一 /finance/<name>/...，组件懒加载
//     menu: { group: '财务', subgroup: '税务填报', title, icon, path, order },  // 侧边栏菜单项
//   }   // subgroup 可选：组内二级折叠子分组名（如「税务填报」）；缺省则直挂在分组下
const files = import.meta.glob('./*/manifest.js', { eager: true })

// 注册唯一性校验：缺 name / 重名 / 路由 path 冲突的模块跳过并告警，
// 避免下个模块接入时静默消失或覆盖前一个（L-138）。
export function dedupeManifests(list, warn = (message) => console.warn(`[modules] ${message}`)) {
  const names = new Set()
  const paths = new Set()
  const routeNames = new Set()
  const out = []
  for (const manifest of list || []) {
    if (!manifest || !manifest.name) {
      warn('模块缺少 name，已跳过')
      continue
    }
    if (names.has(manifest.name)) {
      warn(`模块重名已跳过：${manifest.name}`)
      continue
    }
    const conflict = (manifest.routes || []).map((r) => r.path).find((path) => path && paths.has(path))
    if (conflict) {
      warn(`模块 ${manifest.name} 的路由 ${conflict} 已被占用，已跳过`)
      continue
    }
    const conflictName = (manifest.routes || [])
      .map((r) => r.name)
      .find((name) => name && routeNames.has(name))
    if (conflictName) {
      warn(`模块 ${manifest.name} 的路由名 ${conflictName} 已被占用，已跳过`)
      continue
    }
    names.add(manifest.name)
    for (const route of manifest.routes || []) {
      if (route.path) paths.add(route.path)
      if (route.name) routeNames.add(route.name)
    }
    out.push(manifest)
  }
  return out
}

const rawManifests = []

for (const [path, mod] of Object.entries(files)) {
  const dir = path.split('/')[1]
  if (dir.startsWith('_')) continue
  rawManifests.push(mod.default)
}

const manifests = dedupeManifests(rawManifests)

// 侧栏分组顺序：与《使用说明》的菜单地图一致（调试组固定最后，避免 80 < 81 把职能挤到后面）
const GROUP_ORDER = new Map([
  ['财务', 10],
  ['经营', 20],
  ['职能', 30],
  ['调试', 90],
])

export function groupRank(name) {
  return GROUP_ORDER.get(name) ?? 100
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
    .map((group, index) => ({ group, index }))
    .sort((a, b) => groupRank(a.group.name) - groupRank(b.group.name) || a.index - b.index)
    .map(({ group }) => group)
}
