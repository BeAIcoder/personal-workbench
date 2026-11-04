/**
 * 发票明细按字段提交 + 响应序号。
 *
 * 旧实现 blur 一次提交 project_name+remark 两个字段：先编辑项目、再编辑备注时，
 * 先发的请求晚返回会把备注写回旧值。这里每次只提交被编辑的字段，
 * 并用「明细+字段」序号丢弃旧响应；最新一次失败交给调用方重新加载恢复。
 */
export function createItemPatcher({ send, onApplied, onError }) {
  const seqByKey = new Map()

  async function patch(itemId, field, value) {
    const key = `${itemId}:${field}`
    const seq = (seqByKey.get(key) || 0) + 1
    seqByKey.set(key, seq)
    try {
      const result = await send(itemId, { [field]: value })
      if (seqByKey.get(key) !== seq) return { applied: false, stale: true, result }
      // 只回传被编辑字段：调用方即使整体 merge 也不会把旧响应里的其它字段写回去
      const patchResult = result && Object.prototype.hasOwnProperty.call(result, field)
        ? { [field]: result[field] }
        : {}
      onApplied?.(itemId, patchResult)
      return { applied: true, result }
    } catch (error) {
      if (seqByKey.get(key) === seq) await onError?.(itemId, field, error)
      return { applied: false, error }
    }
  }

  return { patch }
}
