// 档案库查询/预览结果行扁平化：el-table 的 prop 经 lodash get 解析为「深层路径」，
// 直接把列名当 prop 会踩两个坑——列名含点号（a.b）被当成 obj.a.b 取不到值；
// 预览接口返回的行本身是数组（[[v1,v2],...]），任何列名都取不到值。
// 统一按列序映射到安全键 c0/c1/...，模板用同样的键做 prop。
export function columnKeys(columns) {
  return (columns || []).map((_, i) => `c${i}`)
}

export function toTableRows(columns, rows) {
  const keys = columnKeys(columns)
  if (!keys.length) return []
  return (rows || []).map((row) => {
    const obj = {}
    keys.forEach((k, i) => {
      obj[k] = Array.isArray(row) ? row[i] : row?.[k]
    })
    return obj
  })
}
