// 财报导入结果口径（纯函数，供视图与 Vitest 共用）。
// 后端 success 只表示「解析成功」，是否写入 mod_tax_financial_data 由 persisted 决定；
// 未落库时不得显示成功，否则用户会误以为 DQ/汇算已能取数。

export function importOutcome(result) {
  if (!result) return { tagType: 'info', tagText: '待导入' }
  if (!result.success) return { tagType: 'danger', tagText: '解析失败' }
  if (!result.persisted) return { tagType: 'warning', tagText: '仅解析未落库' }
  return { tagType: 'success', tagText: '已落库' }
}

// toast 只有「已落库」才算成功；仅解析/失败都按 warning 提醒用户看明细。
export function importToastType(result) {
  if (!result) return 'info'
  return result.success && result.persisted ? 'success' : 'warning'
}
