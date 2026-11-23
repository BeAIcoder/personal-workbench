<template>
  <div class="page">
    <HelpLink section="6.12" />
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>残保金填报（财政部公告2023年第8号）</span>
          <el-button type="primary" size="small" :loading="saving" @click="submit">
            {{ revisingId ? '保存更正' : '计算并保存' }}
          </el-button>
        </div>
      </template>
      <el-alert v-if="revisingId" type="warning" :closable="false" class="mt8"
        :title="`正在更正记录 #${revisingId}：不允许更换用人单位或所属年度，保存后由后端重算；已导出/已提交的记录后端会拒绝更正`" />
      <el-button v-if="revisingId" size="small" text class="mt8" @click="cancelRevise">取消更正</el-button>
      <el-form :model="form" label-width="150px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="缴费所属年度">
              <el-date-picker v-model="form.year" type="year" value-format="YYYY"
                placeholder="年度" :disabled="Boolean(revisingId)" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="用人单位">
              <el-input v-model="form.taxpayer_name" :disabled="Boolean(revisingId)" />
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
        <el-form-item label="当地上年度社平工资">
          <el-input-number v-model="form.local_avg_wage" :min="1" :controls="false" placeholder="可空" style="width: 200px" />
          <span class="hint">元/年，可空；填写后按财税〔2015〕98 号「年平均工资 2 倍」封顶计征，不填时后端给未校验上限的 warning</span>
        </el-form-item>
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

function blankForm() {
  return {
    year: '', taxpayer_name: '', total_employees: 30, total_salary: 0,
    disabled_employees: 0, ratio: 0.015, local_avg_wage: null,
  }
}

const form = ref(blankForm())
const list = ref([])
const saving = ref(false)
const resultTitle = ref('')
const resultType = ref('success')
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

async function loadList() {
  try {
    const { data } = await api.get('/finance/dsf/filings')
    list.value = data.items || []
  } catch { /* 拦截器已提示 */ }
}

// 受控更正：回填原申报参数，提交走 PUT /filings/{id}；
// 已导出/已提交由后端 409 拒绝，提示交给拦截器，视图不重复 toast。
function startRevise(row) {
  const inputs = row.inputs || {}
  form.value = {
    year: row.year,
    taxpayer_name: row.taxpayer_name || '',
    total_employees: inputs.total_employees ?? 1,
    total_salary: inputs.total_salary ?? 0,
    disabled_employees: inputs.disabled_employees ?? 0,
    ratio: inputs.ratio ?? 0.015,
    // 社平工资是「2 倍封顶」的唯一依据：不回填会把 API 建档的封顶记录重置为 0/None
    local_avg_wage: inputs.local_avg_wage ?? null,
  }
  revisingId.value = row.id
  resultTitle.value = ''
}

function cancelRevise() {
  revisingId.value = null
  form.value = blankForm()
  resultTitle.value = ''
}

async function submit() {
  if (!form.value.year) {
    ElMessage.warning('请选择缴费所属年度')
    return
  }
  saving.value = true
  try {
    const revising = revisingId.value
    const { data } = revising
      ? await api.put(`/finance/dsf/filings/${revising}`, form.value)
      : await api.post('/finance/dsf/filings', form.value)
    if (revising) ElMessage.success('已更正并重算')
    const c = data.calc_result || {}
    resultType.value = c.exempt ? 'success' : 'warning'
    resultTitle.value = c.exempt ? c.note : `${c.note}；应缴费额 ¥${(c.payable ?? 0).toLocaleString()}`
    revisingId.value = null
    await loadList()
  } catch {
    /* 拦截器已提示；已导出/已提交 409 保持更正态，便于按文案调整或删除重填 */
  } finally {
    saving.value = false
  }
}

async function exportXlsx(row) {
  try {
    await api.post(`/finance/dsf/filings/${row.id}/export`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

function download(row) {
  window.open(`${api.defaults.baseURL}/finance/dsf/filings/${row.id}/download`, '_blank')
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除 ${row.year} 年度残保金填报记录？删除后不可恢复。`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/finance/dsf/filings/${row.id}`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

onMounted(loadList)
</script>

<style scoped>
</style>
