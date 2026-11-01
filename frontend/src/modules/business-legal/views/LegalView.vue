<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>法务管理</span>
          <div>
            <el-button size="small" @click="loadAll">刷新</el-button>
            <el-button size="small" @click="exportFile('contracts.xlsx')">导出合同</el-button>
            <el-button size="small" @click="exportFile('cases.xlsx')">导出案件</el-button>
            <AskAiButton :prompt="aiPrompt" />
          </div>
        </div>
      </template>

      <el-row :gutter="12" class="kpi">
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">待审 / 高风险</div><div class="kpi-value">{{ summary.pending_reviews }} / {{ summary.high_risk }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">30 天内到期合同</div><div class="kpi-value">{{ summary.expiring_30 }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">未结案件 / 7 天开庭</div><div class="kpi-value">{{ summary.open_cases }} / {{ summary.hearings_7 }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">未结标的额</div><div class="kpi-value">¥{{ fmt(summary.case_amount) }}</div></div></el-col>
      </el-row>

      <el-tabs v-model="tab" class="mt8">
        <el-tab-pane label="合同审查" name="reviews">
          <div class="filters">
            <el-input v-model="reviewQuery.keyword" placeholder="合同标题" size="small" clearable style="width: 170px" @keyup.enter="loadReviews" />
            <el-select v-model="reviewQuery.status" placeholder="状态" size="small" clearable style="width: 120px">
              <el-option v-for="s in reviewStatuses" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
            <el-select v-model="reviewQuery.risk_level" placeholder="风险" size="small" clearable style="width: 100px">
              <el-option label="低" value="low" /><el-option label="中" value="medium" /><el-option label="高" value="high" />
            </el-select>
            <el-button size="small" type="primary" @click="loadReviews">筛选</el-button>
            <el-button size="small" type="success" @click="openReview()">新建审查</el-button>
          </div>
          <el-table :data="reviews" size="small" border>
            <el-table-column prop="title" label="合同标题" min-width="180" show-overflow-tooltip />
            <el-table-column prop="contract_type" label="类型" width="100" />
            <el-table-column prop="counterparty" label="对方" min-width="130" show-overflow-tooltip />
            <el-table-column label="金额" width="120" align="right"><template #default="{ row }">¥{{ fmt(row.amount) }}</template></el-table-column>
            <el-table-column prop="submit_date" label="送审" width="110" />
            <el-table-column label="风险" width="80">
              <template #default="{ row }"><el-tag size="small" :type="riskTag(row.risk_level)">{{ row.risk_label }}</el-tag></template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag size="small" :type="reviewTag(row.status)">{{ row.status_label }}</el-tag></template>
            </el-table-column>
            <el-table-column label="到期" width="120">
              <template #default="{ row }">
                {{ row.expire_date || '—' }}
                <el-tag v-if="row.expiring" size="small" type="warning">临期</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openReview(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeReview(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="案件" name="cases">
          <div class="filters">
            <el-input v-model="caseQuery.keyword" placeholder="案件标题" size="small" clearable style="width: 170px" @keyup.enter="loadCases" />
            <el-select v-model="caseQuery.stage" placeholder="阶段" size="small" clearable style="width: 110px">
              <el-option v-for="s in stages" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
            <el-select v-model="caseQuery.case_type" placeholder="类型" size="small" clearable style="width: 100px">
              <el-option label="诉讼" value="litigation" /><el-option label="仲裁" value="arbitration" /><el-option label="纠纷" value="dispute" />
            </el-select>
            <el-button size="small" type="primary" @click="loadCases">筛选</el-button>
            <el-button size="small" type="success" @click="openCase()">新建案件</el-button>
          </div>
          <el-table :data="cases" size="small" border>
            <el-table-column prop="case_no" label="案号" width="140" />
            <el-table-column prop="title" label="案件标题" min-width="170" show-overflow-tooltip />
            <el-table-column prop="case_type_label" label="类型" width="80" />
            <el-table-column prop="counterparty" label="对方" min-width="120" show-overflow-tooltip />
            <el-table-column label="标的额" width="120" align="right"><template #default="{ row }">¥{{ fmt(row.amount) }}</template></el-table-column>
            <el-table-column label="阶段" width="90">
              <template #default="{ row }"><el-tag size="small" :type="stageTag(row.stage)">{{ row.stage_label }}</el-tag></template>
            </el-table-column>
            <el-table-column label="开庭" width="120">
              <template #default="{ row }">
                {{ row.hearing_date || '—' }}
                <el-tag v-if="row.hearing_soon" size="small" type="danger">临近</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="result" label="结果" min-width="120" show-overflow-tooltip />
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openCase(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeCase(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="用印台账" name="seals">
          <div class="filters">
            <el-input v-model="sealQuery.keyword" placeholder="用途关键词" size="small" clearable style="width: 180px" @keyup.enter="loadSeals" />
            <el-button size="small" type="primary" @click="loadSeals">筛选</el-button>
            <el-button size="small" type="success" @click="openSeal()">新建用印</el-button>
          </div>
          <el-table :data="seals" size="small" border>
            <el-table-column prop="purpose" label="用途" min-width="200" show-overflow-tooltip />
            <el-table-column prop="doc_type" label="文件类型" width="110" />
            <el-table-column prop="applicant" label="申请人" width="100" />
            <el-table-column prop="approver" label="审批人" width="100" />
            <el-table-column prop="used_at" label="用印日期" width="120" />
            <el-table-column prop="copies" label="份数" width="80" />
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openSeal(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeSeal(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="reviewDialog" :title="reviewForm.id ? '编辑合同审查' : '新建合同审查'" width="620px">
      <el-form :model="reviewForm" label-width="90px">
        <el-form-item label="合同标题" required><el-input v-model="reviewForm.title" /></el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="合同类型"><el-input v-model="reviewForm.contract_type" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="对方"><el-input v-model="reviewForm.counterparty" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="金额"><el-input-number v-model="reviewForm.amount" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="送审日期"><el-date-picker v-model="reviewForm.submit_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="审查人"><el-input v-model="reviewForm.reviewer" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="风险等级"><el-select v-model="reviewForm.risk_level"><el-option label="低" value="low" /><el-option label="中" value="medium" /><el-option label="高" value="high" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="状态"><el-select v-model="reviewForm.status"><el-option v-for="s in reviewStatuses" :key="s.value" :label="s.label" :value="s.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="到期日"><el-date-picker v-model="reviewForm.expire_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="备注"><el-input v-model="reviewForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviewDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveReview">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="caseDialog" :title="caseForm.id ? '编辑案件' : '新建案件'" width="620px">
      <el-form :model="caseForm" label-width="90px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="案号"><el-input v-model="caseForm.case_no" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="类型"><el-select v-model="caseForm.case_type"><el-option label="诉讼" value="litigation" /><el-option label="仲裁" value="arbitration" /><el-option label="纠纷" value="dispute" /></el-select></el-form-item></el-col>
        </el-row>
        <el-form-item label="案件标题" required><el-input v-model="caseForm.title" /></el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="对方"><el-input v-model="caseForm.counterparty" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="标的额"><el-input-number v-model="caseForm.amount" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="阶段"><el-select v-model="caseForm.stage"><el-option v-for="s in stages" :key="s.value" :label="s.label" :value="s.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="立案日"><el-date-picker v-model="caseForm.filed_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="开庭日"><el-date-picker v-model="caseForm.hearing_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="结果"><el-input v-model="caseForm.result" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="caseForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="caseDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveCase">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="sealDialog" :title="sealForm.id ? '编辑用印' : '新建用印'" width="520px">
      <el-form :model="sealForm" label-width="90px">
        <el-form-item label="用途" required><el-input v-model="sealForm.purpose" /></el-form-item>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="文件类型"><el-input v-model="sealForm.doc_type" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="份数"><el-input-number v-model="sealForm.copies" :min="1" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="申请人"><el-input v-model="sealForm.applicant" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="审批人"><el-input v-model="sealForm.approver" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="用印日期"><el-date-picker v-model="sealForm.used_at" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="备注"><el-input v-model="sealForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sealDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveSeal">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'
import AskAiButton from '../../../components/assistant/AskAiButton.vue'

const aiPrompt = computed(() => '请分析法务台账：合同审查积压与风险分布、案件阶段与开庭提醒、用印合规情况')

const loading = ref(false)
const saving = ref(false)
const tab = ref('reviews')
const reviewStatuses = [
  { value: 'pending', label: '待审' },
  { value: 'reviewing', label: '审查中' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已驳回' },
  { value: 'archived', label: '已归档' },
]
const stages = [
  { value: 'filed', label: '立案' },
  { value: 'hearing', label: '开庭' },
  { value: 'judgment', label: '判决' },
  { value: 'appeal', label: '上诉' },
  { value: 'closed', label: '结案' },
]

const summary = ref({ pending_reviews: 0, high_risk: 0, expiring_30: 0, open_cases: 0, hearings_7: 0, case_amount: 0 })
const reviews = ref([])
const cases = ref([])
const seals = ref([])
const reviewQuery = reactive({ keyword: '', status: '', risk_level: '' })
const caseQuery = reactive({ keyword: '', stage: '', case_type: '' })
const sealQuery = reactive({ keyword: '' })

const reviewDialog = ref(false)
const reviewForm = reactive({
  id: null, title: '', contract_type: '', counterparty: '', amount: 0,
  submit_date: '', reviewer: '', status: 'pending', risk_level: 'low', expire_date: '', note: '',
})
const caseDialog = ref(false)
const caseForm = reactive({
  id: null, case_no: '', title: '', case_type: 'dispute', counterparty: '', amount: 0,
  stage: 'filed', filed_date: '', hearing_date: '', result: '', note: '',
})
const sealDialog = ref(false)
const sealForm = reactive({ id: null, purpose: '', doc_type: '', applicant: '', approver: '', used_at: '', copies: 1, note: '' })

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}
function riskTag(r) {
  return { low: 'success', medium: 'warning', high: 'danger' }[r] || 'info'
}
function reviewTag(s) {
  return { pending: 'warning', reviewing: 'primary', approved: 'success', rejected: 'danger', archived: 'info' }[s] || 'info'
}
function stageTag(s) {
  return { filed: 'info', hearing: 'warning', judgment: 'primary', appeal: 'danger', closed: 'success' }[s] || 'info'
}

async function loadSummary() {
  try {
    const { data } = await api.get('/business/legal/summary')
    summary.value = data
  } catch { /* 拦截器已提示 */ }
}
async function loadReviews() {
  loading.value = true
  try {
    const { data } = await api.get('/business/legal/contracts', { params: reviewQuery })
    reviews.value = data.items || []
  } catch { /* 拦截器已提示 */ } finally { loading.value = false }
}
async function loadCases() {
  loading.value = true
  try {
    const { data } = await api.get('/business/legal/cases', { params: caseQuery })
    cases.value = data.items || []
  } catch { /* 拦截器已提示 */ } finally { loading.value = false }
}
async function loadSeals() {
  try {
    const { data } = await api.get('/business/legal/seals', { params: sealQuery })
    seals.value = data.items || []
  } catch { /* 拦截器已提示 */ }
}
async function loadAll() {
  await loadSummary()
  await Promise.all([loadReviews(), loadCases(), loadSeals()])
}

function openReview(row) {
  Object.assign(reviewForm, row ? {
    id: row.id, title: row.title, contract_type: row.contract_type, counterparty: row.counterparty,
    amount: row.amount, submit_date: row.submit_date || '', reviewer: row.reviewer,
    status: row.status, risk_level: row.risk_level, expire_date: row.expire_date || '', note: row.note,
  } : {
    id: null, title: '', contract_type: '', counterparty: '', amount: 0,
    submit_date: '', reviewer: '', status: 'pending', risk_level: 'low', expire_date: '', note: '',
  })
  reviewDialog.value = true
}
async function saveReview() {
  if (saving.value) return
  if (!reviewForm.title.trim()) { ElMessage.warning('合同标题不能为空'); return }
  saving.value = true
  try {
    const payload = { ...reviewForm, submit_date: reviewForm.submit_date || null, expire_date: reviewForm.expire_date || null }
    delete payload.id
    if (reviewForm.id) await api.put(`/business/legal/contracts/${reviewForm.id}`, payload)
    else await api.post('/business/legal/contracts', payload)
    reviewDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally { saving.value = false }
}
async function removeReview(row) {
  try { await ElMessageBox.confirm(`确认删除「${row.title}」？`, '删除确认', { type: 'warning' }) } catch { return }
  try {
    await api.delete(`/business/legal/contracts/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

function openCase(row) {
  Object.assign(caseForm, row ? {
    id: row.id, case_no: row.case_no, title: row.title, case_type: row.case_type,
    counterparty: row.counterparty, amount: row.amount, stage: row.stage,
    filed_date: row.filed_date || '', hearing_date: row.hearing_date || '',
    result: row.result, note: row.note,
  } : {
    id: null, case_no: '', title: '', case_type: 'dispute', counterparty: '', amount: 0,
    stage: 'filed', filed_date: '', hearing_date: '', result: '', note: '',
  })
  caseDialog.value = true
}
async function saveCase() {
  if (saving.value) return
  if (!caseForm.title.trim()) { ElMessage.warning('案件标题不能为空'); return }
  saving.value = true
  try {
    const payload = { ...caseForm, filed_date: caseForm.filed_date || null, hearing_date: caseForm.hearing_date || null }
    delete payload.id
    if (caseForm.id) await api.put(`/business/legal/cases/${caseForm.id}`, payload)
    else await api.post('/business/legal/cases', payload)
    caseDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally { saving.value = false }
}
async function removeCase(row) {
  try { await ElMessageBox.confirm(`确认删除案件「${row.title}」？`, '删除确认', { type: 'warning' }) } catch { return }
  try {
    await api.delete(`/business/legal/cases/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

function openSeal(row) {
  Object.assign(sealForm, row ? { ...row, used_at: row.used_at || '' } :
    { id: null, purpose: '', doc_type: '', applicant: '', approver: '', used_at: '', copies: 1, note: '' })
  sealDialog.value = true
}
async function saveSeal() {
  if (saving.value) return
  if (!sealForm.purpose.trim()) { ElMessage.warning('用印用途不能为空'); return }
  saving.value = true
  try {
    const payload = { ...sealForm, used_at: sealForm.used_at || null }
    delete payload.id
    if (sealForm.id) await api.put(`/business/legal/seals/${sealForm.id}`, payload)
    else await api.post('/business/legal/seals', payload)
    sealDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally { saving.value = false }
}
async function removeSeal(row) {
  try { await ElMessageBox.confirm(`确认删除用印「${row.purpose}」？`, '删除确认', { type: 'warning' }) } catch { return }
  try {
    await api.delete(`/business/legal/seals/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

function exportFile(name) {
  window.open(`${api.defaults.baseURL}/business/legal/export/${name}`, '_blank')
  ElMessage.info('正在导出')
}
onMounted(loadAll)
</script>

<style scoped>
.kpi { margin-bottom: 8px; }
</style>
