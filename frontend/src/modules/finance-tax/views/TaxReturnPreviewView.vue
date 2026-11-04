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
        <el-descriptions-item label="季度">{{ detail.quarter ? `Q${detail.quarter}` : '年度' }}</el-descriptions-item>
        <el-descriptions-item label="申报类型">{{ detail.return_type }}</el-descriptions-item>
        <el-descriptions-item label="适用优惠">{{ detail.tax_type || '—' }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="detail.status === 'submitted' ? 'success' : 'info'" size="small">
            {{ detail.status === 'submitted' ? '已提交' : '草稿' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="应纳税所得额">{{ detail.taxable_income?.toFixed(2) }} 元</el-descriptions-item>
        <el-descriptions-item label="应纳税额">{{ detail.tax_amount?.toFixed(2) }} 元</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ detail.created_at }}</el-descriptions-item>
      </el-descriptions>

      <h4 style="margin-top: 20px">利润表口径（A200000 第 1/2/3 行来源）</h4>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="营业收入">{{ yuan(detail.operating_revenue) }}</el-descriptions-item>
        <el-descriptions-item label="其他收入">{{ yuan(detail.other_income) }}</el-descriptions-item>
        <el-descriptions-item label="营业成本">{{ yuan(detail.operating_cost) }}</el-descriptions-item>
        <el-descriptions-item label="期间费用">{{ yuan(detail.total_expenses) }}</el-descriptions-item>
        <el-descriptions-item label="利润总额">{{ yuan(detail.total_profit) }}</el-descriptions-item>
        <el-descriptions-item label="纳税调整净额">{{ yuan(detail.tax_adjustments) }}</el-descriptions-item>
      </el-descriptions>
      <p class="hint">
        逐项纳税调整明细不在此处留存（历史「调整明细」接口恒为空，已按输入契约移除）；
        明细请在「税额计算」页按同一组参数查看「计算过程」。
      </p>
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
const enterpriseName = ref('')

const yuan = (value) => `${Number(value || 0).toFixed(2)} 元`

async function load() {
  loading.value = true
  try {
    const { data } = await api.get(`/finance/tax/tax-returns/${route.params.id}`)
    detail.value = data
    if (data.enterprise_id) {
      const e = await api.get(`/finance/tax/enterprises/${data.enterprise_id}`)
      enterpriseName.value = e.data.name
    }
  } catch {
    /* 保持空态 */
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.hint {
  margin: 12px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
