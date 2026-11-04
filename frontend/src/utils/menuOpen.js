// 由路由路径反查需要展开的侧栏分组/二级子分组索引。
// 供 AppLayout 处理「应用内深链」：挂载后的路由变化也要展开父级，
// 但调用方必须自行排除用户手动折叠过的索引。
export function menuOpenIndices(path, moduleGroups = [], staticGroups = []) {
  const hit = (items) => (items || []).some((m) => m.path === path)
  const opened = []
  for (const group of moduleGroups || []) {
    const groupItems = group.items || []
    const subgroups = group.subgroups || []
    if (hit(groupItems) || subgroups.some((sg) => hit(sg.items))) {
      opened.push(`group-${group.name}`)
      for (const sg of subgroups) {
        if (hit(sg.items)) opened.push(`subgroup-${group.name}-${sg.name}`)
      }
    }
  }
  for (const group of staticGroups || []) {
    if (hit(group.items)) opened.push(`group-${group.name}`)
  }
  return opened
}
