// 由路由路径反查需要展开的侧栏分组/二级子分组索引，以及当前应高亮的菜单项。
// 匹配规则是「最长路径前缀」：详情子路由（/finance/expense/runs/42）命中其父菜单项
// （/finance/expense），而不是只认精确相等；前缀按路径段边界判断，/finance/expenseX
// 不会误命中 /finance/expense。供 AppLayout 处理刷新、深链与应用内跳转。
// 调用方必须自行排除用户手动折叠过的索引。

// 去掉 query/hash 与末尾斜杠（根路径保留），容忍调用方直接传 location 风格路径。
export function normalizeMenuPath(path) {
  if (typeof path !== 'string' || !path) return ''
  const clean = path.split('?')[0].split('#')[0]
  if (!clean) return ''
  return clean.length > 1 ? clean.replace(/\/+$/, '') : clean
}

function matchesPath(menuPath, path) {
  if (!menuPath || !path) return false
  // 根路径只做精确匹配，否则所有未知路由都会高亮「工作台概览」
  if (menuPath === '/') return path === '/'
  return path === menuPath || path.startsWith(`${menuPath}/`)
}

// 每条菜单项带上需要展开的祖先索引；直挂项只展开一级分组。
function menuEntries(moduleGroups = [], staticGroups = []) {
  const entries = []
  for (const group of staticGroups || []) {
    for (const item of group.items || []) {
      entries.push({ path: normalizeMenuPath(item.path), open: [`group-${group.name}`] })
    }
  }
  for (const group of moduleGroups || []) {
    const groupIndex = `group-${group.name}`
    for (const item of group.items || []) {
      entries.push({ path: normalizeMenuPath(item.path), open: [groupIndex] })
    }
    for (const subgroup of group.subgroups || []) {
      const subgroupIndex = `subgroup-${group.name}-${subgroup.name}`
      for (const item of subgroup.items || []) {
        entries.push({ path: normalizeMenuPath(item.path), open: [groupIndex, subgroupIndex] })
      }
    }
  }
  return entries.filter((entry) => entry.path)
}

// 返回全部「最长前缀」命中项；同长度重复路径（理论上注册中心已去重）一并保留。
function longestHits(path, entries) {
  const target = normalizeMenuPath(path)
  if (!target) return []
  let bestLength = -1
  let hits = []
  for (const entry of entries) {
    if (!matchesPath(entry.path, target)) continue
    if (entry.path.length > bestLength) {
      bestLength = entry.path.length
      hits = [entry]
    } else if (entry.path.length === bestLength) {
      hits.push(entry)
    }
  }
  return hits
}

export function menuOpenIndices(path, moduleGroups = [], staticGroups = []) {
  const opened = []
  for (const hit of longestHits(path, menuEntries(moduleGroups, staticGroups))) {
    for (const index of hit.open) {
      if (!opened.includes(index)) opened.push(index)
    }
  }
  return opened
}

// 当前高亮菜单项：在全部菜单（含一级静态菜单）中取最长前缀，未命中返回空串。
export function menuActivePath(path, topMenus = [], moduleGroups = [], staticGroups = []) {
  const entries = [
    ...(topMenus || []).map((item) => ({ path: normalizeMenuPath(item.path), open: [] })),
    ...menuEntries(moduleGroups, staticGroups),
  ].filter((entry) => entry.path)
  const hits = longestHits(path, entries)
  return hits.length ? hits[0].path : ''
}
