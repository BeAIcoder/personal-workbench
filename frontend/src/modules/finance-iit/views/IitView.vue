<template>
  <div class="page">
    <el-card shadow="never">
      <template #header><span>个人所得税填报（工资薪金累计预扣法）</span></template>
      <el-form :model="form" label-width="110px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="所属年度">
              <el-date-picker v-model="form.year" type="year" value-format="YYYY" placeholder="年度" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="扣缴义务人">
              <el-input v-model="form.taxpayer_name" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-divider>员工月度明细（元/月）</el-divider>
        <el-table :data="form.rows" size="small" border>
          <el-table-column label="月份" width="100">
            <template #default="{ row }">
              <el-input-number v-model="row.month" :min="1" :max="12" :controls="false" style="width: 100%" />
            </template>
          </el-table-column>
          <el-table-column label="员工" width="120">
            <template #default="{ row }">
              <el-input v-model="row.name" />
            </template>
          </el-table-column>
          <el-table-column label="工资" width="150">
            <template #default="{ row }">
              <el-input-number v-model="row.salary" :min="0" :controls="false" style="width: 100%" />
            </template>
          </el-table-column>
          <el-table-column label="社保公积金" width="150">
            <template #default="{ row }">
              <el-input-number v-model="row.social_insurance" :min="0" :controls="false" style="width: 100%" />
            </template>
          </el-table-column>
          <el-table-column label="专项附加" width="150">
            <template #default="{ row }">
              <el-input-number v-model="row.special_additional" :min="0" :controls="false" style="width: 100" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80">
            <template #default="{ $index }">
              <el-button size="small" text type="danger" @click="form.rows.splice($index, 1)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-button size="small" class="mt8" @click="addRow">+ 加一行</el-button>
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
        <el-table-column prop="year" label="年度" width="90" />
        <el-table-column prop="taxpayer_name" label="扣缴义务人" />
        <el-table-column label="合计应预扣" width="140">
          <template #default="{ row }">¥{{ (row.calc_result?.totals?.tax_withheld ?? 0).toLocaleString() }}</template>
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
  year: '', taxpayer_name: '',
  rows: [],
})
const list = ref([])
const saving = ref(false)
const resultTitle = ref('')

function addRow() {
  form.value.rows.push({ month: 1, name: '', salary: 0, social_insurance: 0, special_additional: 0, other_deductions: 0 })
}

async function submit() {
  if (!form.value.year || !form.value.rows.length) {
    ElMessage.warning('请选年度并至少加一行员工月度明细')
    return
  }
  saving.value = true
  try {
    const { data } = await api.post('/finance/iit/filings', form.value)
    const t = data.calc_result?.totals?.tax_withheld ?? 0
    resultTitle.value = `合计应预扣预缴 ¥${t.toLocaleString()}（按累计预扣法逐月模拟）`
    await loadList()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function loadList() {
  try {
    const { data } = await api.get('/finance/iit/filings')
    list.value = data.items || []
  } catch { /* 拦截器已提示 */ }
}

async function exportXlsx(row) {
  try {
    await api.post(`/finance/iit/filings/${row.id}/export`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

function download(row) {
  window.open(`${api.defaults.baseURL}/finance/iit/filings/${row.id}/download`, '_blank')
}

async function remove(row) {
  try {
    await api.delete(`/finance/iit/filings/${row.id}`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

onMounted(loadList)
</script>

<style scoped>
.page { padding: 0; }
.mt8 { margin-top: 8px; }
</style>
