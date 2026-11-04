/**
 * 分页列表共用逻辑（HR / 法务 / 发票批次等）：
 * - 请求参数统一带 page/page_size（后端响应返回 total/page/page_size）；
 * - 响应归一，缺字段时安全回退，不显示 NaN；
 * - 删除最后一条时计算应回退到的页码。
 */
export const DEFAULT_PAGE_SIZE = 20

function positiveInt(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) && n >= 1 ? Math.trunc(n) : fallback
}

export function toPageParams(query, page, pageSize) {
  return {
    ...(query || {}),
    page: positiveInt(page, 1),
    page_size: positiveInt(pageSize, DEFAULT_PAGE_SIZE),
  }
}

export function pageState(data, fallback = {}) {
  // 兼容旧后端：裸数组按「单页列表」处理，避免静默显示空表
  const legacyArray = Array.isArray(data)
  const items = Array.isArray(data?.items) ? data.items : legacyArray ? data : []
  const totalRaw = Number(data?.total)
  const total = Number.isFinite(totalRaw) ? Math.max(0, Math.trunc(totalRaw)) : items.length
  return {
    items,
    total,
    page: positiveInt(data?.page ?? fallback.page, 1),
    pageSize: positiveInt(data?.page_size ?? fallback.pageSize, DEFAULT_PAGE_SIZE),
  }
}

export function totalPages(total, pageSize) {
  const size = positiveInt(pageSize, DEFAULT_PAGE_SIZE)
  return Math.max(1, Math.ceil(Math.max(0, Number(total) || 0) / size))
}

export function nextPageAfterDelete(page, total, pageSize, removed = 1) {
  const remaining = Math.max(0, (Number(total) || 0) - Math.max(0, Number(removed) || 0))
  return Math.min(positiveInt(page, 1), totalPages(remaining, pageSize))
}
