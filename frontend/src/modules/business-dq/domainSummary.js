/** DQ 每域 E/W/I 文案：与后端域对象的 error/warning/info 计数同源（L-176）。 */
export function domainSummary(row) {
  if (!row?.available) return '未就绪'
  return `E${row.error ?? 0}/W${row.warning ?? 0}/I${row.info ?? 0}`
}
