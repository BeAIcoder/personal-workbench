<template>
  <el-autocomplete
    ref="inputRef"
    v-model="keyword"
    :fetch-suggestions="fetchSuggestions"
    :trigger-on-focus="false"
    placeholder="全局搜索任务 / 日程 / 笔记（Ctrl + K）"
    clearable
    style="width: 340px"
    @select="onSelect"
  >
    <template #default="{ item }">
      <div class="search-row">
        <el-tag :type="tagType[item.type] || 'info'" size="small" effect="plain">
          {{ tagLabel[item.type] || '提示' }}
        </el-tag>
        <span class="search-title">{{ item.title }}</span>
        <span class="search-snippet">{{ item.snippet }}</span>
      </div>
    </template>
  </el-autocomplete>
</template>

<script>
// 全局搜索的防抖调度：输入停止 SEARCH_DEBOUNCE_MS 后才发起请求，
// 避免每次击键都打 /api/search；与竞态守卫 createLatestGuard 配合，
// 只有最后一次请求的结果允许回调（慢响应不覆盖新关键词的建议）。
export const SEARCH_DEBOUNCE_MS = 300

export function createDebouncer(delay = SEARCH_DEBOUNCE_MS) {
  let timer = null
  return {
    schedule(fn) {
      if (timer !== null) clearTimeout(timer)
      timer = setTimeout(() => {
        timer = null
        fn()
      }, delay)
    },
    cancel() {
      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }
    },
  }
}
</script>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { searchApi } from '../api'
import {
  buildSuggestionItems,
  createLatestGuard,
  keywordAfterSuggestion,
  parseSearchDate,
} from '../utils/search'

const router = useRouter()
const keyword = ref('')
const inputRef = ref(null)
const lastQuery = ref('')
// 慢响应守卫：输入变化后只接受最后一次请求的建议，避免旧结果覆盖新关键词
const searchGuard = createLatestGuard()
// 防抖：连续输入合并为一次请求；空白输入立即清空并取消在途调度
const debounce = createDebouncer()

const tagLabel = { task: '任务', schedule: '日程', note: '笔记' }
const tagType = { task: 'primary', schedule: 'warning', note: 'success' }
const targetPath = { task: '/tasks', schedule: '/schedules', note: '/notes' }

async function fetchSuggestions(queryString, cb) {
  const q = queryString.trim()
  if (!q) {
    // 空白输入立即清空建议，并取消在途防抖，避免旧关键词稍后弹出
    debounce.cancel()
    cb([])
    return
  }
  debounce.schedule(() => runSearch(q, cb))
}

async function runSearch(q, cb) {
  const token = searchGuard.next()
  lastQuery.value = q
  try {
    const { data } = await searchApi.query(q)
    if (!searchGuard.isCurrent(token)) return
    cb(buildSuggestionItems(data))
  } catch {
    if (searchGuard.isCurrent(token)) cb([])
  }
}

function onSelect(item) {
  if (!item.type) {
    // 占位项不可跳转，同时把输入框恢复为关键词（Element Plus 已先把 value 写进 v-model）
    keyword.value = keywordAfterSuggestion(item, lastQuery.value)
    return
  }
  const query = { q: keyword.value.trim() }
  const date = item.type === 'schedule' ? parseSearchDate(item.time) : null
  if (date) query.date = date
  router.push({ path: targetPath[item.type], query })
}

function focus() {
  inputRef.value?.focus()
}

// 组件卸载时取消在途防抖，避免卸载后仍发起搜索请求
onBeforeUnmount(() => debounce.cancel())

defineExpose({ focus })
</script>
