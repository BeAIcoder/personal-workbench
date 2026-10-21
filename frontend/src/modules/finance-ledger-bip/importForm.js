// BIP 导入向导的纯逻辑：人工指定类型时的映射字段清单与确认条件。
// 预览接口对无法自动识别的文件不返回 required_fields / mapping，
// 前端需按用户指定的类型自行给出映射表单，否则确认入库必然因缺映射被后端 400 拒绝。
const REQUIRED_FIELDS = {
  voucher: ['date', 'ledger', 'voucher_no'],
  aux: ['subject_code', 'subject_name', 'date', 'ledger', 'voucher_no'],
  balance: ['subject_code', 'subject_name'],
}

// 人工指定类型时可映射的字段：必填字段在前，金额列与摘要在后（均可留空，由用户自选）
const MANUAL_FIELDS = {
  voucher: ['date', 'ledger', 'voucher_no', 'debit', 'credit', 'summary'],
  aux: ['subject_code', 'subject_name', 'date', 'ledger', 'voucher_no', 'debit', 'credit', 'summary'],
  balance: ['subject_code', 'subject_name'],
}

const PERIOD_RE = /^\d{4}-(0[1-9]|1[0-2])$/

// 当前应渲染的列映射字段：自动识别类型用预览下发的 required_fields；
// 未识别类型按用户指定的类型给出手动映射清单
export function mappingFields(preview, fileType) {
  if (!preview) return []
  if (preview.file_type !== 'unknown') return preview.required_fields || []
  return MANUAL_FIELDS[fileType] || []
}

// 确认入库按钮的启用条件（与后端 _merge_mapping 校验口径一致）
export function canConfirmImport(preview, form) {
  if (!preview) return false
  const manual = preview.file_type === 'unknown'
  const fileType = form.file_type || preview.file_type
  if (manual && !form.file_type) return false
  if (fileType === 'balance' && !PERIOD_RE.test(form.period || '')) return false
  // 必填字段：自动识别用预览下发的 required_fields；人工指定用本地清单（金额列/摘要不强制）
  const required = manual ? REQUIRED_FIELDS[fileType] || [] : preview.required_fields || []
  if (!required.every((f) => form.mapping?.[f])) return false
  // 人工指定类型时金额列没有自动建议，必须至少映射一个（凭证/明细账）
  if (manual && (fileType === 'voucher' || fileType === 'aux') && !(form.mapping?.debit || form.mapping?.credit)) {
    return false
  }
  return true
}
