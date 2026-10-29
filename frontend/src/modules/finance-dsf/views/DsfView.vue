<template>
  <div class="page">
    <el-card shadow="never">
      <template #header><span>残保金填报（财政部公告2023年第8号）</span></template>
      <el-form :model="form" label-width="150px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="缴费所属年度">
              <el-date-picker v-model="form.year" type="year" value-format="YYYY" placeholder="年度" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="用人单位">
              <el-input v-model="form.taxpayer_name" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="在职职工总数">
              <el-input-number v-model="form.total_employees" :min="1" :controls="false" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="上年在职职工工资总额">
              <el-input-number v-model="form.total_salary" :min="0" :controls="false" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="安排残疾人就业人数">
              <el-input-number v-model="form.disabled_employees" :min="0" :controls="false" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="所在地规定比例">
          <el-input-number v-model="form.ratio" :min="0.01" :max="0.5" :step="0.005" :controls="false" style="width: 120px" />
          <span class="hint">默认 1.5%，各地可不同</span>
        </el-form-item>
        <div class="mt8">
          <el-button type="primary" :loading="saving" @click="submit">计算并保存</el-button>
        </div>
      </el-form>
      <el-alert v-if="resultTitle" :title="resultTitle" :type="resultType" :closable="false" class="mt8" />
    </el-card>

    <el-card shadow="never" class="mt8">
      <template #header><span>填报记录</span></template>
      <el-table :data="list" size="small" border>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="year" label="年度" width="90" />
        <el-table-column prop="taxpayer_name" label="用人单位" />
        <el-table-column label="应缴费额" width="140">
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
  year: '', taxpayer_name: '', total_employees: 30, total_salary: 0,
  disabled_employees: 0, ratio: 0.015,
})
const list = ref([])
const saving = ref(false)
const resultTitle = ref('')
const resultType = ref('success')

async function loadList() {
  const { data } = await api.get('/finance/dsf/filings')
  list.value = data.items || []
}

async function submit() {
  if (!form.value.year) {
    ElMessage.warning('请选择缴费所属年度')
    return
  }
  saving.value = true
  try {
    const { data } = await api.post('/finance/dsf/filings', form.value)
    const c = data.calc_result
    resultType.value = c.exempt ? 'success' : 'warning'
    resultTitle.value = c.exempt ? c.note : `${c.note}；应缴费额 ¥${c.payable.toLocaleString()}`
    await loadList()
  } finally {
    saving.value = false
  }
}

async function exportXlsx(row) {
  await api.post(`/finance/dsf/filings/${row.id}/export`)
  await loadList()
}

function download(row) {
  window.open(`${api.defaults.baseURL}/finance/dsf/filings/${row.id}/download`, '_blank')
}

async function remove(row) {
  await api.delete(`/finance/dsf/filings/${row.id}`)
  await loadList()
}

onMounted(loadList)
</script>

<style scoped>
.page { padding: 0; }
.mt8 { margin-top: 8px; }
.hint { margin-left: 8px; font-size: 12px; color: #909399; }
</style>
