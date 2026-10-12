<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>申报记录</span>
          <el-select v-model="enterpriseId" placeholder="全部企业" size="small" clearable style="width: 220px" @change="reload">
            <el-option v-for="e in enterprises" :key="e.id" :label="e.name" :value="e.id" />
          </el-select>
        </div>
      </template>
      <el-table :data="items" size="small" empty-text="暂无申报记录（在「税额计算」页保存生成）">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column label="企业" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ enterpriseName(row.enterprise_id) }}</template>
        </el-table-column>
        <el-table-column prop="year" label="年度" width="80" />
        <el-table-column prop="quarter" label="季度" width="70">
          <template #default="{ row }">{{ row.quarter ?? '年度' }}</template>
        </el-table-column>
        <el-table-column prop="return_type" label="类型" width="80" />
        <el-table-column label="应纳税所得额" width="130" align="right">
          <template #default="{ row }">{{ row.taxable_income?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="应纳税额" width="120" align="right">
          <template #default="{ row }">{{ row.tax_amount?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'submitted' ? 'success' : 'info'" size="small">
              {{ row.status === 'submitted' ? '已提交' : '草稿' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="$router.push({ name: 'finance-tax-return-detail', params: { id: row.id } })">预览</el-button>
            <el-button v-if="row.status !== 'submitted'" size="small" link type="primary" @click="submit(row)">提交</el-button>
            <el-button size="small" link type="primary" @click="exportFile(row.id, 'excel')">Excel</el-button>
            <el-button size="small" link type="primary" @click="exportFile(row.id, 'pdf')">PDF</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'

const loading = ref(false)
const items = ref([])
const enterprises = ref([])
const enterpriseId = ref(undefined)

const enterpriseName = (id) => enterprises.value.find((e) => e.id === id)?.name || `#${id}`

async function reload() {
  loading.value = true
  try {
    const params = {}
    if (enterpriseId.value) params.enterprise_id = enterpriseId.value
    const { data } = await api.get('/finance/tax/tax-returns/', { params })
    items.value = data
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

async function submit(row) {
  await ElMessageBox.confirm(`提交 ${row.year} 年申报记录？提交后不可修改。`, '提交确认', { type: 'warning' })
  await api.put(`/finance/tax/tax-returns/${row.id}/submit`)
  ElMessage.success('已提交')
  await reload()
}

function exportFile(id, kind) {
  window.open(`/api/finance/tax/tax-returns/${id}/export/${kind}`, '_blank')
}

onMounted(async () => {
  const { data } = await api.get('/finance/tax/enterprises/')
  enterprises.value = data
  await reload()
})
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
