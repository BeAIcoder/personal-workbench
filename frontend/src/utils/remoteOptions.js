// 关联下拉的远程搜索契约：后端分页上限 200，前端取 50 条并提示用关键字缩小范围。
export const OPTION_PAGE_SIZE = 50

export function optionParams(keyword = '', pageSize = OPTION_PAGE_SIZE) {
  const params = { page: 1, page_size: pageSize }
  const kw = String(keyword ?? '').trim()
  if (kw) params.keyword = kw
  return params
}

export function optionCapHint(items, total, limit = OPTION_PAGE_SIZE) {
  const loaded = Array.isArray(items) ? items.length : 0
  const count = Number(total) || 0
  if (count <= limit || loaded >= count) return ''
  return `仅显示前 ${limit} 项（共 ${count} 项），请输入关键字缩小范围`
}

// 编辑已有记录时，当前选中项可能不在最近一次搜索结果里；缺了就补到首位，
// 否则 el-select 只显示数字 id。已有同 id 时保持原数组引用。
export function ensureOption(options, option) {
  const list = options || []
  if (!option || option.id == null) return list
  if (list.some((item) => item.id === option.id)) return list
  return [option, ...list]
}

// 远程搜索防抖 + 令牌：连续输入只请求最后一次；已过期的响应不得再写回选项。
export function createRemoteOptionSearch(load, delay = 250) {
  let timer = null
  let seq = 0
  return {
    search(keyword) {
      if (timer !== null) clearTimeout(timer)
      const token = ++seq
      timer = setTimeout(() => {
        timer = null
        load(keyword, token)
      }, delay)
      return token
    },
    isCurrent(token) {
      return token === seq
    },
    cancel() {
      if (timer !== null) clearTimeout(timer)
      timer = null
      seq += 1
    },
  }
}
