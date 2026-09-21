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

const router = useRouter()
const keyword = ref('')
const inputRef = ref(null)

const tagLabel = { task: '任务', schedule: '日程', note: '笔记' }
const tagType = { task: 'primary', schedule: 'warning', note: 'success' }
const targetPath = { task: '/tasks', schedule: '/schedules', note: '/notes' }

async function fetchSuggestions(queryString, cb) {
  const q = queryString.trim()
  if (!q) {
    cb([])
    return
  }
  try {
    const { data } = await searchApi.query(q)
    const flat = [...data.tasks, ...data.schedules, ...data.notes].map((x) => ({
      ...x,
      value: x.title,
    }))
    if (!flat.length) {
      cb([{ value: '未找到相关内容', title: '未找到相关内容', snippet: '', type: null }])
    } else {
      cb(flat)
    }
  } catch {
    cb([])
  }
}

function onSelect(item) {
  if (!item.type) return
  router.push({ path: targetPath[item.type], query: { q: keyword.value.trim() } })
}

function focus() {
  inputRef.value?.focus()
}

defineExpose({ focus })
</script>
