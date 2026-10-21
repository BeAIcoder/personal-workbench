<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>前端报错（{{ total }}）</span>
          <div class="head-ops">
            <span class="auto-label">自动刷新</span>
            <el-switch v-model="autoRefresh" size="small" />
            <el-button size="small" @click="load">刷新</el-button>
            <el-button size="small" type="danger" :disabled="!items.length" @click="clearAll">清空</el-button>
          </div>
        </div>
      </template>
      <el-empty v-if="!items.length" description="暂无前端报错（全局 errorHandler / window.onerror / unhandledrejection 自动采集）" />
      <el-table v-else :data="items" size="small">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="stack-wrap">
              <pre v-if="row.stack" class="stack-pre">{{ row.stack }}</pre>
              <p v-else class="no-stack">无堆栈</p>
              <p v-if="row.ua" class="ua-line">{{ row.ua }}</p>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="ts" label="时间" width="170" />
        <el-table-column prop="page" label="页面" width="160" show-overflow-tooltip />
        <el-table-column prop="message" label="错误信息" min-width="320" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { debugApi } from '../../../api'

const REFRESH_INTERVAL = 10000

const loading = ref(false)
const items = ref([])
const total = ref(0)
const autoRefresh = ref(false)
let timer = null

async function load() {
  loading.value = true
  try {
    const { data } = await debugApi.clientErrors.list(200)
    // 新的报错排前面
    items.value = [...(data.items || [])].reverse()
    total.value = data.total ?? items.value.length
  } catch {
    items.value = []
    total.value = 0  // 计数随列表一起归零，避免表头残留上一次的条数
  } finally {
    loading.value = false
  }
}

async function clearAll() {
  try {
    await ElMessageBox.confirm('确定清空全部前端报错记录？', '清空确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await debugApi.clientErrors.clear()
    ElMessage.success('已清空')
    await load()
  } catch {
    /* 拦截器已提示 */
  }
}

watch(autoRefresh, (on) => {
  if (on) {
    timer = setInterval(load, REFRESH_INTERVAL)
  } else if (timer) {
    clearInterval(timer)
    timer = null
  }
})

onMounted(load)
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.head-ops {
  display: flex;
  align-items: center;
  gap: 8px;
}

.auto-label {
  font-size: 12px;
  color: #909399;
}

.stack-wrap {
  padding: 4px 16px;
}

.stack-pre {
  background: #f5f7fa;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow: auto;
  margin: 0;
}

.no-stack {
  color: #c0c4cc;
  font-size: 12px;
  margin: 0;
}

.ua-line {
  color: #909399;
  font-size: 12px;
  margin: 6px 0 0;
}
</style>
