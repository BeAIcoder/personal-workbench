<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>企业列表</span>
          <div>
            <el-button size="small" @click="$router.push({ name: 'finance-tax-import' })">财报导入</el-button>
            <el-button size="small" type="primary" @click="$router.push({ name: 'finance-tax-enterprise-new' })">新建企业</el-button>
          </div>
        </div>
      </template>
      <el-table :data="items" size="small" empty-text="暂无企业，点右上角新建（或先用财报导入自动创建）">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="name" label="企业名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="tax_number" label="纳税人识别号" min-width="180" />
        <el-table-column prop="industry_type" label="行业" min-width="100" />
        <el-table-column prop="employee_count" label="从业人数" width="90" align="right" />
        <el-table-column label="资产总额(万)" width="110" align="right">
          <template #default="{ row }">{{ row.total_assets?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="限制行业" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_restricted_industry ? 'warning' : 'success'" size="small">
              {{ row.is_restricted_industry ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="goCalculate(row.id)">税额计算</el-button>
            <el-button size="small" link type="primary" @click="goEdit(row.id)">编辑</el-button>
            <el-button size="small" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'

const router = useRouter()
const loading = ref(false)
const items = ref([])

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/tax/enterprises/')
    items.value = data
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

const goCalculate = (id) => router.push({ name: 'finance-tax-calculate', params: { enterpriseId: id } })
const goEdit = (id) => router.push({ name: 'finance-tax-enterprise-edit', params: { id } })

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除企业「${row.name}」将同时删除其申报记录，确认？`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  await api.delete(`/finance/tax/enterprises/${row.id}`)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
