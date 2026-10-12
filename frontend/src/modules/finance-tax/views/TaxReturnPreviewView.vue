<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>申报预览 #{{ detail.id }}</span>
          <el-button size="small" @click="$router.push({ name: 'finance-tax-returns' })">返回</el-button>
        </div>
      </template>
      <el-descriptions :column="3" border>
        <el-descriptions-item label="企业">{{ enterpriseName }}</el-descriptions-item>
        <el-descriptions-item label="年度">{{ detail.year }}</el-descriptions-item>
        <el-descriptions-item label="季度">{{ detail.quarter ?? '年度' }}</el-descriptions-item>
        <el-descriptions-item label="申报类型">{{ detail.return_type }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="detail.status === 'submitted' ? 'success' : 'info'" size="small">
            {{ detail.status === 'submitted' ? '已提交' : '草稿' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="应纳税所得额">{{ detail.taxable_income?.toFixed(2) }} 万元</el-descriptions-item>
        <el-descriptions-item label="应纳税额">{{ detail.tax_amount?.toFixed(2) }} 万元</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ detail.created_at }}</el-descriptions-item>
      </el-descriptions>

      <h4 style="margin-top: 20px">纳税调整明细</h4>
      <el-table :data="adjustments" size="small" empty-text="无调整明细">
        <el-table-column prop="item_name" label="调整项目" min-width="160" />
        <el-table-column prop="adjustment_type" label="调整类型" width="120" />
        <el-table-column label="金额(万元)" width="120" align="right">
          <template #default="{ row }">{{ row.amount?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="原因" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../../../api'

const route = useRoute()
const loading = ref(false)
const detail = ref({})
const adjustments = ref([])
const enterpriseName = ref('')

async function load() {
  loading.value = true
  try {
    const { data } = await api.get(`/finance/tax/tax-returns/${route.params.id}`)
    detail.value = data
    if (data.enterprise_id) {
      const e = await api.get(`/finance/tax/enterprises/${data.enterprise_id}`)
      enterpriseName.value = e.data.name
    }
    const adj = await api.get(`/finance/tax/tax-returns/${route.params.id}/adjustments`)
    adjustments.value = adj.data?.items || adj.data || []
  } catch {
    /* 保持空态 */
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
