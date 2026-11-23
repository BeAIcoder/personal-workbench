<template>
  <div class="page">
    <HelpLink section="6.11" />
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>个人所得税填报（工资薪金累计预扣法）</span>
          <el-button type="primary" size="small" :loading="saving" @click="submit">
            {{ revisingId ? '保存更正' : '计算并保存' }}
          </el-button>
        </div>
      </template>
      <el-alert v-if="revisingId" type="warning" :closable="false" class="mt8"
        :title="`正在更正记录 #${revisingId}：不允许更换扣缴义务人或所属年度，保存后由后端重算；已导出/已提交的记录后端会拒绝更正`" />
      <el-button v-if="revisingId" size="small" text class="mt8" @click="cancelRevise">取消更正</el-button>
      <el-form :model="form" label-width="110px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="所属年度">
              <el-date-picker v-model="form.year" type="year" value-format="YYYY"
                placeholder="年度" :disabled="Boolean(revisingId)" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="扣缴义务人">
              <el-input v-model="form.taxpayer_name" :disabled="Boolean(revisingId)" />
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
          <el-table-column label="工号/证件号" width="140">
            <template #default="{ row }">
              <el-input v-model="row.employee_id" placeholder="同名员工必填" />
            </template>
          </el-table-column>
          <el-table-column label="任职起始月" width="110">
            <template #default="{ row }">
              <el-input-number v-model="row.employment_start_month" :min="1" :max="12" :controls="false" style="width: 100%" />
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
              <el-input-number v-model="row.special_additional" :min="0" :controls="false" style="width: 100%" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80">
            <template #default="{ $index }">
              <el-button size="small" text type="danger" @click="form.rows.splice($index, 1)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-button size="small" class="mt8" @click="addRow">+ 加一行</el-button>
      </el-form>
      <el-alert v-if="resultTitle" :title="resultTitle" type="success" :closable="false" class="mt8" />
      <el-alert v-for="w in warnings" :key="w" :title="w" type="warning" :closable="false" class="mt8" />
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
        <el-table-column label="操作" width="280">
          <template #default="{ row }">
            <el-button size="small" text :disabled="!row.has_export" @click="download(row)">下载底稿</el-button>
            <el-button size="small" text type="primary" :disabled="row.has_export" @click="exportXlsx(row)">生成底稿</el-button>
            <el-button size="small" text type="primary" :disabled="reviseBlocked(row)" :title="reviseHint(row)" @click="startRevise(row)">更正</el-button>
            <el-button size="small" text type="danger" :disabled="deleteBlocked(row)" :title="deleteBlocked(row) ? '已提交的申报不可删除' : ''" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import HelpLink from '../../../components/HelpLink.vue'
import api from '../../../api'

const form = ref({
  year: '', taxpayer_name: '',
  rows: [],
})
const list = ref([])
const saving = ref(false)
const resultTitle = ref('')
const warnings = ref([])
const revisingId = ref(null)

// N-6：已提交/已导出不允许更正（后端 ensure_revisable 同口径），已提交不可删除；
// 已导出底稿仍允许删除后重填（与后端删除口径一致）。
function reviseBlocked(row) {
  return Boolean(row) && (row.status === 'submitted' || Boolean(row.has_export))
}

function deleteBlocked(row) {
  return Boolean(row) && row.status === 'submitted'
}

function reviseHint(row) {
  if (!row) return ''
  if (row.status === 'submitted') return '已提交，不能更正或删除；如需变更请联系主管税务机关'
  if (row.has_export) return '已导出底稿，不能直接更正；请先删除该记录后重新填报'
  return ''
}

function blankForm() {
  return { year: '', taxpayer_name: '', rows: [] }
}

function addRow() {
  form.value.rows.push({
    month: 1, name: '', employee_id: '', employment_start_month: 1,
    salary: 0, social_insurance: 0, special_additional: 0, other_deductions: 0,
  })
}

// 受控更正：回填原申报明细，提交走 PUT /filings/{id}；
// 已导出/已提交由后端 409 拒绝，提示交给拦截器，视图不重复 toast。
function startRevise(row) {
  form.value = {
    year: row.year,
    taxpayer_name: row.taxpayer_name || '',
    rows: (row.rows || []).map((r) => ({ ...r })),
  }
  revisingId.value = row.id
  resultTitle.value = ''
  warnings.value = []
}

function cancelRevise() {
  revisingId.value = null
  form.value = blankForm()
  resultTitle.value = ''
  warnings.value = []
}

async function submit() {
  if (!form.value.year || !form.value.rows.length) {
    ElMessage.warning('请选年度并至少加一行员工月度明细')
    return
  }
  saving.value = true
  try {
    const revising = revisingId.value
    const { data } = revising
      ? await api.put(`/finance/iit/filings/${revising}`, form.value)
      : await api.post('/finance/iit/filings', form.value)
    if (revising) ElMessage.success('已更正并重算')
    warnings.value = data.calc_result?.warnings || []
    const t = data.calc_result?.totals?.tax_withheld ?? 0
    resultTitle.value = `合计应预扣预缴 ¥${t.toLocaleString()}（按累计预扣法逐月模拟）`
    revisingId.value = null
    await loadList()
  } catch {
    /* 拦截器已提示；已导出/已提交 409 保持更正态，便于按文案调整或删除重填 */
  } finally {
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
    await ElMessageBox.confirm(`确认删除 ${row.year} 年度个税填报记录？删除后不可恢复。`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/finance/iit/filings/${row.id}`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

onMounted(loadList)
</script>

<style scoped>
</style>
