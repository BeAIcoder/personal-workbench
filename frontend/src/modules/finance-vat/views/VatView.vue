<template>
  <div class="page">
    <el-card shadow="never">
      <template #header><span>增值税填报（一般纳税人/小规模）</span></template>
      <el-form :model="form" label-width="110px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="6">
            <el-form-item label="所属期">
              <el-date-picker v-model="form.period" type="month" value-format="YYYY-MM" placeholder="所属期" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="纳税人类型">
              <el-radio-group v-model="form.taxpayer_type">
                <el-radio value="general">一般纳税人</el-radio>
                <el-radio value="small_scale">小规模</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="纳税人名称">
              <el-input v-model="form.taxpayer_name" />
            </el-form-item>
          </el-col>
        </el-row>
        <template v-if="form.taxpayer_type === 'general'">
          <el-divider>销项明细</el-divider>
          <el-table :data="form.output_items" size="small" border>
            <el-table-column label="业务类型" width="200">
              <template #default="{ row }">
                <el-input v-model="row.business_type" />
              </template>
            </el-table-column>
            <el-table-column label="销售额" width="180">
              <template #default="{ row }">
                <el-input-number v-model="row.amount" :min="0" :controls="false" style="width: 100%" />
              </template>
            </el-table-column>
            <el-table-column label="税率" width="120">
              <template #default="{ row }">
                <el-select v-model="row.rate">
                  <el-option :value="0.13" label="13%" />
                  <el-option :value="0.09" label="9%" />
                  <el-option :value="0.06" label="6%" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{ $index }">
                <el-button size="small" text type="danger" @click="form.output_items.splice($index, 1)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-button size="small" class="mt8" @click="form.output_items.push({ business_type: '销售货物', amount: 0, rate: 0.13 })">+ 加一行</el-button>
          <el-form-item label="进项税额" class="mt8" style="width: 260px">
            <el-input-number v-model="form.input_vat" :min="0" :controls="false" style="width: 100%" />
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="季度销售额" style="width: 280px">
            <el-input-number v-model="form.sales_amount" :min="0" :controls="false" style="width: 100%" />
          </el-form-item>
          <el-alert type="info" :closable="false" title="季度 ≤30 万免征；超 30 万全额按 1%（19号公告至 2027-12-31）。" />
        </template>
        <div class="mt8">
          <el-button type="primary" :loading="saving" @click="submit">计算并保存</el-button>
        </div>
      </el-form>
      <el-alert v-if="resultTitle" :title="resultTitle" type="success" :closable="false" class="mt8" />
    </el-card>

    <el-card shadow="never" class="mt8">
      <template #header><span>填报记录</span></template>
      <el-table :data="list" size="small" border>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="period" label="所属期" width="100" />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">{{ row.taxpayer_type === 'general' ? '一般' : '小规模' }}</template>
        </el-table-column>
        <el-table-column label="应纳税额" width="130">
          <template #default="{ row }">¥{{ (row.calc_result?.payable ?? 0).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button size="small" text :disabled="!row.has_export" @click="download(row)">下载底稿</el-button>
            <el-button size="small" text type="primary" :disabled="row.has_export" @click="exportXlsx(row)">生成底稿</el-button>
            <el-button size="small" text type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const form = ref({
  period: '', taxpayer_type: 'general', taxpayer_name: '',
  output_items: [], input_vat: 0, sales_amount: 0, levy_rate: 0.01,
})
const list = ref([])
const saving = ref(false)
const resultTitle = ref('')

async function loadList() {
  const { data } = await api.get('/finance/vat/filings')
  list.value = data.items || []
}

async function submit() {
  if (!form.value.period) {
    ElMessage.warning('请先选择所属期')
    return
  }
  saving.value = true
  try {
    const { data } = await api.post('/finance/vat/filings', form.value)
    const c = data.calc_result
    resultTitle.value = data.taxpayer_type === 'general'
      ? `销项 ¥${c.output_vat} − 进项 ¥${c.input_vat} = 应纳税额 ¥${c.payable}`
      : (c.exempt ? '季度销售额未超 30 万，免征' : `应纳税额 ¥${c.payable}`)
    await loadList()
  } finally {
    saving.value = false
  }
}

async function exportXlsx(row) {
  await api.post(`/finance/vat/filings/${row.id}/export`)
  await loadList()
}

function download(row) {
  window.open(`${api.defaults.baseURL}/finance/vat/filings/${row.id}/download`, '_blank')
}

async function remove(row) {
  await api.delete(`/finance/vat/filings/${row.id}`)
  await loadList()
}

onMounted(loadList)
</script>

<style scoped>
.page { padding: 0; }
.mt8 { margin-top: 8px; }
</style>
