<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>统计</span>
          <el-radio-group v-model="months" size="small" @change="load">
            <el-radio-button :value="6">近 6 月</el-radio-button>
            <el-radio-button :value="12">近 12 月</el-radio-button>
          </el-radio-group>
        </div>
      </template>

      <div ref="trendChart" style="height: 320px" />
      <div ref="pieChart" style="height: 360px; margin-top: 16px" />
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header><span>当月支出分类构成（{{ stats.month }}）</span></template>
      <el-table :data="stats.by_category" size="small" empty-text="当月无支出">
        <el-table-column prop="name" label="分类" min-width="140" />
        <el-table-column label="金额" width="140" align="right">
          <template #default="{ row }">{{ row.amount.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="占比" width="140" align="right">
          <template #default="{ row }">{{ formatPercent(row.percent) }}%</template>
        </el-table-column>
        <el-table-column label="" min-width="200">
          <template #default="{ row }">
            <el-progress :percentage="Math.min(100, +formatPercent(row.percent))" :stroke-width="8" />
          </template>
        </el-table-column>
      </el-table>
      <div class="summary">
        收入 <b class="income">{{ stats.income?.toFixed(2) }}</b> ｜
        支出 <b class="expense">{{ stats.expense?.toFixed(2) }}</b> ｜
        结余 <b>{{ stats.balance?.toFixed(2) }}</b>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import * as echarts from 'echarts'
import api from '../../../api'
import { localMonth } from '../../_shared/dates'
import { formatPercent } from '../utils'

const loading = ref(false)
const months = ref(6)
const stats = reactive({ month: '', income: 0, expense: 0, balance: 0, by_category: [] })
const trendChart = ref(null)
const pieChart = ref(null)
let trendInst = null
let pieInst = null

async function load() {
  loading.value = true
  try {
    const [m, t] = await Promise.all([
      api.get('/finance/ledger/personal/stats/monthly', { params: { month: localMonth() } }),
      api.get('/finance/ledger/personal/stats/trend', { params: { months: months.value } }),
    ])
    Object.assign(stats, m.data)
    renderCharts(t.data)
  } catch {
    /* 后端未启动时保持空白 */
  } finally {
    loading.value = false
  }
}

function renderCharts(trend) {
  trendInst ??= echarts.init(trendChart.value)
  trendInst.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['收入', '支出'] },
    xAxis: { type: 'category', data: trend.map((i) => i.month) },
    yAxis: { type: 'value' },
    series: [
      { name: '收入', type: 'bar', data: trend.map((i) => i.income), itemStyle: { color: '#67c23a' } },
      { name: '支出', type: 'bar', data: trend.map((i) => i.expense), itemStyle: { color: '#f56c6c' } },
    ],
  })
  pieInst ??= echarts.init(pieChart.value)
  pieInst.setOption({
    tooltip: { trigger: 'item', formatter: '{b}<br/>{c}（{d}%）' },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        label: { formatter: '{b}\n{d}%' },
        data: stats.by_category.map((c) => ({ name: c.name, value: c.amount })),
      },
    ],
  })
}

onMounted(load)
onUnmounted(() => {
  trendInst?.dispose()
  pieInst?.dispose()
})
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.summary {
  margin-top: 12px;
  color: #606266;
}

.income {
  color: #67c23a;
}

.expense {
  color: #f56c6c;
}
</style>
