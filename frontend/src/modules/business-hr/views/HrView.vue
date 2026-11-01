<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>行政人事</span>
          <div>
            <el-button size="small" @click="loadAll">刷新</el-button>
            <el-button size="small" @click="exportFile('employees.xlsx')">导出花名册</el-button>
            <AskAiButton :prompt="aiPrompt" />
          </div>
        </div>
      </template>

      <el-row :gutter="12" class="kpi">
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">在职 / 试用</div><div class="kpi-value">{{ summary.headcount }} / {{ summary.probation }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">30 天内到期</div><div class="kpi-value">{{ summary.expiring_30 }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">当月薪资人数</div><div class="kpi-value">{{ summary.payroll_count }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">当月实发 / 个税</div><div class="kpi-value">¥{{ fmt(summary.payroll_net) }} / ¥{{ fmt(summary.payroll_tax) }}</div></div></el-col>
      </el-row>

      <el-tabs v-model="tab" class="mt8">
        <el-tab-pane label="员工花名册" name="employees">
          <div class="filters">
            <el-input v-model="empQuery.keyword" placeholder="姓名" size="small" clearable style="width: 130px" @keyup.enter="loadEmployees" />
            <el-input v-model="empQuery.department" placeholder="部门" size="small" clearable style="width: 130px" />
            <el-select v-model="empQuery.status" placeholder="状态" size="small" clearable style="width: 110px">
              <el-option label="在职" value="active" /><el-option label="试用" value="probation" /><el-option label="离职" value="left" />
            </el-select>
            <el-button size="small" type="primary" @click="loadEmployees">筛选</el-button>
            <el-button size="small" type="success" @click="openEmployee()">新建员工</el-button>
            <el-button size="small" @click="loadExpiring">查 30 天到期</el-button>
          </div>
          <el-alert v-if="expiring.length" :title="`30 天内试用/合同到期 ${expiring.length} 人`" type="warning" :closable="false" class="mb8">
            <span v-for="e in expiring" :key="e.id" class="expire-item">{{ e.name }}（{{ e.expiring_types.join('/') }}）</span>
          </el-alert>
          <el-table :data="employees" size="small" border>
            <el-table-column prop="emp_no" label="工号" width="100" />
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="department" label="部门" width="120" />
            <el-table-column prop="position" label="岗位" width="120" />
            <el-table-column prop="hire_date" label="入职" width="110" />
            <el-table-column label="试用/合同到期" width="180">
              <template #default="{ row }">{{ row.probation_end || '—' }} / {{ row.contract_end || '—' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag size="small" :type="empTag(row.status)">{{ row.status_label }}</el-tag></template>
            </el-table-column>
            <el-table-column label="基本工资" width="110" align="right"><template #default="{ row }">¥{{ fmt(row.base_salary) }}</template></el-table-column>
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openEmployee(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeEmployee(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="月度考勤" name="attendance">
          <div class="filters">
            <el-date-picker v-model="attMonth" type="month" value-format="YYYY-MM" size="small" style="width: 130px" @change="loadAttendance" />
            <el-select v-model="attForm.emp_id" placeholder="员工" size="small" filterable style="width: 160px">
              <el-option v-for="e in employees" :key="e.id" :label="`${e.emp_no} ${e.name}`" :value="e.id" />
            </el-select>
            <el-input-number v-model="attForm.actual_days" :min="0" :max="31" :controls="false" size="small" style="width: 90px" />
            <span class="muted">实出勤</span>
            <el-input-number v-model="attForm.leave_days" :min="0" :max="31" :controls="false" size="small" style="width: 90px" />
            <span class="muted">请假</span>
            <el-input-number v-model="attForm.overtime_hours" :min="0" :max="500" :controls="false" size="small" style="width: 90px" />
            <span class="muted">加班(h)</span>
            <el-button size="small" type="primary" :loading="saving" @click="saveAttendance">保存考勤</el-button>
          </div>
          <el-table :data="attendance" size="small" border>
            <el-table-column prop="emp_no" label="工号" width="100" />
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column prop="department" label="部门" width="120" />
            <el-table-column prop="month" label="月份" width="100" />
            <el-table-column prop="work_days" label="应出勤" width="90" align="right" />
            <el-table-column prop="actual_days" label="实出勤" width="90" align="right" />
            <el-table-column prop="leave_days" label="请假" width="80" align="right" />
            <el-table-column prop="overtime_hours" label="加班(h)" width="90" align="right" />
            <el-table-column prop="late_count" label="迟到" width="80" align="right" />
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="薪资核算" name="payroll">
          <div class="filters">
            <el-date-picker v-model="payMonth" type="month" value-format="YYYY-MM" size="small" style="width: 130px" @change="loadPayroll" />
            <el-input-number v-model="payForm.allowance" :min="0" :controls="false" size="small" style="width: 100px" />
            <span class="muted">统一补贴</span>
            <el-button size="small" type="primary" :loading="saving" @click="generatePayroll">生成薪资</el-button>
            <el-button size="small" :disabled="!payroll.length" @click="exportPayroll">导出薪资表</el-button>
          </div>
          <el-alert type="info" :closable="false" class="mb8"
            title="个税按累计预扣法；社保默认 10.5%、公积金 7%（基数为社保基数）。最终以财务个税模块/税务申报为准。" />
          <el-table :data="payroll" size="small" border>
            <el-table-column prop="emp_no" label="工号" width="100" />
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column label="应发" width="110" align="right"><template #default="{ row }">¥{{ fmt(row.gross_pay) }}</template></el-table-column>
            <el-table-column label="扣款" width="100" align="right"><template #default="{ row }">¥{{ fmt(row.deduction) }}</template></el-table-column>
            <el-table-column label="社保+公积金" width="130" align="right"><template #default="{ row }">¥{{ fmt(Number(row.social_insurance) + Number(row.housing_fund)) }}</template></el-table-column>
            <el-table-column label="个税" width="100" align="right"><template #default="{ row }">¥{{ fmt(row.tax) }}</template></el-table-column>
            <el-table-column label="实发" width="110" align="right"><template #default="{ row }">¥{{ fmt(row.net_pay) }}</template></el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag size="small" :type="row.status === 'confirmed' ? 'success' : 'info'">{{ row.status_label }}</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="140">
              <template #default="{ row }">
                <el-button size="small" text type="primary" :disabled="row.status === 'confirmed'" @click="confirmPayroll(row)">确认</el-button>
                <el-button size="small" text type="danger" :disabled="row.status === 'confirmed'" @click="removePayroll(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="employeeDialog" :title="employeeForm.id ? '编辑员工' : '新建员工'" width="640px">
      <el-form :model="employeeForm" label-width="100px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="工号" required><el-input v-model="employeeForm.emp_no" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="姓名" required><el-input v-model="employeeForm.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="部门"><el-input v-model="employeeForm.department" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="岗位"><el-input v-model="employeeForm.position" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="入职日期"><el-date-picker v-model="employeeForm.hire_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="试用到期"><el-date-picker v-model="employeeForm.probation_end" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="合同到期"><el-date-picker v-model="employeeForm.contract_end" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="状态"><el-select v-model="employeeForm.status"><el-option label="在职" value="active" /><el-option label="试用" value="probation" /><el-option label="离职" value="left" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="手机"><el-input v-model="employeeForm.phone" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="基本工资"><el-input-number v-model="employeeForm.base_salary" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="社保基数"><el-input-number v-model="employeeForm.social_base" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="专项附加扣除(元/月)"><el-input-number v-model="employeeForm.special_additional" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="备注"><el-input v-model="employeeForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="employeeDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEmployee">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'
import AskAiButton from '../../../components/assistant/AskAiButton.vue'

const aiPrompt = computed(() => '请分析人事台账：花名册与试用期/合同到期分布、考勤完整性、薪资核算口径与个税累计预扣的一致性')

const loading = ref(false)
const saving = ref(false)
const tab = ref('employees')
const currentMonth = new Date().toLocaleDateString('sv-SE').slice(0, 7)

const summary = ref({ headcount: 0, probation: 0, expiring_30: 0, payroll_count: 0, payroll_net: 0, payroll_tax: 0 })
const employees = ref([])
const expiring = ref([])
const attendance = ref([])
const payroll = ref([])
const empQuery = reactive({ keyword: '', department: '', status: '' })
const attMonth = ref(currentMonth)
const payMonth = ref(currentMonth)
const attForm = reactive({ emp_id: null, actual_days: 21.75, leave_days: 0, overtime_hours: 0 })
const payForm = reactive({ allowance: 0 })

const employeeDialog = ref(false)
const employeeForm = reactive({
  id: null, emp_no: '', name: '', department: '', position: '',
  hire_date: '', probation_end: '', contract_end: '', status: 'active',
  phone: '', base_salary: 0, social_base: 0, special_additional: 0, note: '',
})

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}
function empTag(s) {
  return { active: 'success', probation: 'warning', left: 'info' }[s] || 'info'
}

async function loadSummary() {
  try {
    const { data } = await api.get('/business/hr/summary', { params: { month: payMonth.value } })
    summary.value = data
  } catch { /* 拦截器已提示 */ }
}

async function loadEmployees() {
  loading.value = true
  try {
    const { data } = await api.get('/business/hr/employees', { params: empQuery })
    employees.value = data.items || []
  } catch { /* 拦截器已提示 */ } finally { loading.value = false }
}

async function loadExpiring() {
  try {
    const { data } = await api.get('/business/hr/employees/expiring', { params: { days: 30 } })
    expiring.value = data || []
  } catch { /* 拦截器已提示 */ }
}

async function loadAttendance() {
  try {
    const { data } = await api.get('/business/hr/attendance', { params: { month: attMonth.value } })
    attendance.value = data || []
  } catch { /* 拦截器已提示 */ }
}

async function loadPayroll() {
  try {
    const { data } = await api.get('/business/hr/payroll', { params: { month: payMonth.value } })
    payroll.value = data || []
  } catch { /* 拦截器已提示 */ }
}

async function loadAll() {
  await loadSummary()
  await Promise.all([loadEmployees(), loadExpiring(), loadAttendance(), loadPayroll()])
}

function openEmployee(row) {
  Object.assign(employeeForm, row ? {
    id: row.id, emp_no: row.emp_no, name: row.name, department: row.department,
    position: row.position, hire_date: row.hire_date || '', probation_end: row.probation_end || '',
    contract_end: row.contract_end || '', status: row.status, phone: row.phone,
    base_salary: row.base_salary, social_base: row.social_base,
    special_additional: row.special_additional ?? 0, note: row.note,
  } : {
    id: null, emp_no: '', name: '', department: '', position: '',
    hire_date: '', probation_end: '', contract_end: '', status: 'active',
    phone: '', base_salary: 0, social_base: 0, special_additional: 0, note: '',
  })
  employeeDialog.value = true
}

async function saveEmployee() {
  if (saving.value) return
  if (!employeeForm.emp_no.trim() || !employeeForm.name.trim()) {
    ElMessage.warning('工号与姓名不能为空')
    return
  }
  saving.value = true
  try {
    const payload = {
      ...employeeForm,
      hire_date: employeeForm.hire_date || null,
      probation_end: employeeForm.probation_end || null,
      contract_end: employeeForm.contract_end || null,
    }
    delete payload.id
    if (employeeForm.id) await api.put(`/business/hr/employees/${employeeForm.id}`, payload)
    else await api.post('/business/hr/employees', payload)
    employeeDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally { saving.value = false }
}

async function removeEmployee(row) {
  try {
    await ElMessageBox.confirm(`删除员工「${row.name}」会同时删除其考勤与薪资，确认？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/hr/employees/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

async function saveAttendance() {
  if (saving.value) return
  if (!attForm.emp_id) { ElMessage.warning('请选择员工'); return }
  saving.value = true
  try {
    await api.put(`/business/hr/attendance/${attForm.emp_id}/${attMonth.value}`, {
      work_days: 21.75,
      actual_days: attForm.actual_days,
      leave_days: attForm.leave_days,
      overtime_hours: attForm.overtime_hours,
      late_count: 0,
    })
    await Promise.all([loadAttendance(), loadSummary()])
  } catch { /* 拦截器已提示 */ } finally { saving.value = false }
}

async function generatePayroll() {
  if (saving.value) return
  try {
    await ElMessageBox.confirm(`按 ${payMonth.value} 生成全部在职/试用员工薪资？`, '生成确认', { type: 'warning' })
  } catch { return }
  saving.value = true
  try {
    const { data } = await api.post('/business/hr/payroll/generate', {
      month: payMonth.value, allowance: payForm.allowance,
    })
    ElMessage.success(`已生成 ${data.count} 人，实发合计 ¥${fmt(data.total_net)}`)
    if (data.warnings?.length) data.warnings.forEach((message) => ElMessage.warning(message))
    await Promise.all([loadPayroll(), loadSummary()])
  } catch { /* 拦截器已提示 */ } finally { saving.value = false }
}

async function confirmPayroll(row) {
  try {
    await ElMessageBox.confirm(`确认 ${row.name} ${row.month} 薪资？确认后不可删除。`, '确认薪资', { type: 'warning' })
  } catch { return }
  try {
    await api.post(`/business/hr/payroll/${row.id}/confirm`)
    await Promise.all([loadPayroll(), loadSummary()])
  } catch { /* 拦截器已提示 */ }
}

async function removePayroll(row) {
  try {
    await ElMessageBox.confirm(`删除 ${row.name} ${row.month} 的薪资草稿？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/hr/payroll/${row.id}`)
    await Promise.all([loadPayroll(), loadSummary()])
  } catch { /* 拦截器已提示 */ }
}

function exportFile(name) {
  window.open(`${api.defaults.baseURL}/business/hr/export/${name}`, '_blank')
  ElMessage.info('正在导出')
}
function exportPayroll() {
  window.open(`${api.defaults.baseURL}/business/hr/export/payroll.xlsx?month=${payMonth.value}`, '_blank')
  ElMessage.info('正在导出薪资表')
}

onMounted(loadAll)
</script>

<style scoped>
.kpi { margin-bottom: 8px; }
.muted { font-size: 12px; color: var(--el-text-color-secondary); }
.expire-item { margin-right: 12px; }
</style>
