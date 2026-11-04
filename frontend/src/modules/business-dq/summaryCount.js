/**
 * DQ 视图问题计数口径：优先用后端全量 issue_count（Σ 各检查命中数），
 * 后端旧版本没有该字段时回退为 top_issues 的 count 之和。
 */
export function issueTotalOf(data) {
  const total = data?.issue_count
  if (typeof total === 'number' && Number.isFinite(total)) return total
  return (data?.top_issues || []).reduce(
    (sum, item) => sum + (Number(item?.count) || 0),
    0,
  )
}
