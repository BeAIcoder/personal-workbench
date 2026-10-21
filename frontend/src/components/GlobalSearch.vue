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

<script setup>
import { ref } from 'vue'
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

const tagLabel = { task: '任务', schedule: '日程', note: '笔记' }
const tagType = { task: 'primary', schedule: 'warning', note: 'success' }
const targetPath = { task: '/tasks', schedule: '/schedules', note: '/notes' }

async function fetchSuggestions(queryString, cb) {
  const q = queryString.trim()
  const token = searchGuard.next()
  lastQuery.value = q
  if (!q) {
    cb([])
    return
  }
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

defineExpose({ focus })
</script>
