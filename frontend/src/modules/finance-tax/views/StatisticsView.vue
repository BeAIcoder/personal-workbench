<template>
  <div class="page" v-loading="loading">
    <el-row :gutter="16">
      <el-col :span="8">
        <el-card shadow="never">
          <template #header><span>企业数</span></template>
          <el-statistic :value="stats.enterprise_count" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <template #header><span>申报记录数</span></template>
          <el-statistic :value="stats.return_count" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <template #header><span>已提交申报</span></template>
          <el-statistic :value="stats.submitted_count" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header><span>按年度汇总</span></template>
      <el-table :data="byYear" size="small" empty-text="暂无数据">
        <el-table-column prop="year" label="年度" width="120" />
        <el-table-column label="应纳税所得额合计(万)" align="right">
          <template #default="{ row }">{{ row.taxable_income.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="应纳税额合计(万)" align="right">
          <template #default="{ row }">{{ row.tax_amount.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="记录数" width="100" align="right">
          <template #default="{ row }">{{ row.count }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../../../api'

const loading = ref(false)
const enterprises = ref([])
const returns = ref([])

const stats = computed(() => ({
  enterprise_count: enterprises.value.length,
  return_count: returns.value.length,
  submitted_count: returns.value.filter((r) => r.status === 'submitted').length,
}))

const byYear = computed(() => {
  const map = new Map()
  for (const r of returns.value) {
    const cur = map.get(r.year) || { year: r.year, taxable_income: 0, tax_amount: 0, count: 0 }
    cur.taxable_income += r.taxable_income || 0
    cur.tax_amount += r.tax_amount || 0
    cur.count += 1
    map.set(r.year, cur)
  }
  return [...map.values()].sort((a, b) => b.year - a.year)
})

onMounted(async () => {
  loading.value = true
  try {
    const [e, r] = await Promise.all([
      api.get('/finance/tax/enterprises/'),
      api.get('/finance/tax/tax-returns/'),
    ])
    enterprises.value = e.data
    returns.value = r.data
  } catch {
    /* 保持空态 */
  } finally {
    loading.value = false
  }
})
</script>
