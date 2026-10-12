<template>
  <div class="page" v-loading="loading">
    <template v-if="detail">
      <el-card shadow="never">
        <template #header>
          <div class="card-head">
            <span>分类结果 · 运行 #{{ detail.run.id }}</span>
            <div>
              <el-button size="small" @click="$router.push({ name: 'finance-expense' })">返回上传页</el-button>
              <el-button
                size="small"
                type="primary"
                :disabled="detail.run.status !== 'success'"
                @click="download"
              >下载结果 xlsx</el-button>
            </div>
          </div>
        </template>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="原文件名" :span="2">{{ detail.run.original_filename }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(detail.run.status)">{{ statusText(detail.run.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="明细行数">{{ detail.run.total_rows }}</el-descriptions-item>
          <el-descriptions-item label="命中行数">{{ detail.run.matched_rows }}</el-descriptions-item>
          <el-descriptions-item label="命中率">{{ hitRate }}%</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ detail.run.duration_sec }} 秒</el-descriptions-item>
          <el-descriptions-item label="执行时间" :span="2">{{ formatTime(detail.run.created_at) }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card v-if="detail.stats.length" shadow="never" class="block">
        <template #header>
          <span>分类金额占比（贷方本币合计 {{ totalAmount }} 元）</span>
        </template>
        <BaseChart :option="pieOption" height="360px" />
      </el-card>

      <el-card shadow="never" class="block">
        <template #header>
          <div class="card-head">
            <span>分类明细（{{ filteredRows.length }} / {{ detail.rows.length }} 行）</span>
            <el-switch v-model="onlyUnmatched" active-text="只看未命中" />
          </div>
        </template>
        <el-table :data="filteredRows" size="small" max-height="480">
          <el-table-column prop="row_no" label="行号" width="70" />
          <el-table-column prop="summary" label="摘要" min-width="260" show-overflow-tooltip />
          <el-table-column label="金额(元)" width="120" align="right">
            <template #default="{ row }">{{ row.amount.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="命中分类" width="160">
            <template #default="{ row }">
              <el-tag :type="row.hit ? 'primary' : 'warning'">{{ row.category }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
    <el-result v-else-if="failed" icon="error" title="运行记录不存在或加载失败" sub-title="请返回上传页查看最新运行列表" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api, { API_BASE } from '../../../api'
import BaseChart from '../../../components/BaseChart.vue'

const route = useRoute()
const runId = route.params.id

const loading = ref(false)
const failed = ref(false)
const detail = ref(null)
const onlyUnmatched = ref(false)

const filteredRows = computed(() => {
  if (!detail.value) return []
  if (!onlyUnmatched.value) return detail.value.rows
  return detail.value.rows.filter((r) => !r.hit)
})

const hitRate = computed(() => {
  const run = detail.value?.run
  if (!run || !run.total_rows) return '0.0'
  return ((run.matched_rows / run.total_rows) * 100).toFixed(1)
})

const totalAmount = computed(() => {
  if (!detail.value) return '0.00'
  return detail.value.stats.reduce((sum, s) => sum + s.amount, 0).toFixed(2)
})

const pieOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}<br/>{c} 元（{d}%）' },
  legend: { bottom: 0, type: 'scroll' },
  series: [
    {
      type: 'pie',
      radius: ['35%', '65%'],
      center: ['50%', '46%'],
      data: (detail.value?.stats || []).map((s) => ({ name: s.category, value: s.amount })),
      label: { formatter: '{b} {d}%' },
    },
  ],
}))

async function load() {
  loading.value = true
  failed.value = false
  try {
    const { data } = await api.get(`/finance/expense/runs/${runId}`)
    detail.value = data
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

function download() {
  window.open(`${API_BASE}/finance/expense/runs/${runId}/download`, '_blank')
}

function statusType(s) {
  return s === 'success' ? 'success' : s === 'failed' ? 'danger' : 'info'
}

function statusText(s) {
  return { success: '成功', failed: '失败', running: '执行中' }[s] || s
}

function formatTime(t) {
  return t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : ''
}

onMounted(load)
</script>

<style scoped>
.page {
  max-width: 960px;
}

.block {
  margin-top: 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
