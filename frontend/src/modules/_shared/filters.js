// 列表筛选共用逻辑：变更筛选条件后先复位页码再重新加载，
// 否则停留在旧 page 会出现"空表错觉"
export function applyFilters(page, reload) {
  page.value = 1
  return reload()
}
