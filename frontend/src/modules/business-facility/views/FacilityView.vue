<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>工程物业</span>
          <div>
            <el-button size="small" @click="loadAll">刷新</el-button>
            <el-button size="small" @click="exportEquipment">导出设备</el-button>
            <el-button size="small" @click="exportCompliance">导出合规台账</el-button>
          </div>
        </div>
      </template>

      <el-row :gutter="12" class="kpi">
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">设备总数 / A 类</div><div class="kpi-value">{{ summary.equipment_count }} / {{ summary.category_a }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">30 天内到期</div><div class="kpi-value">{{ summary.compliance_30 }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">已逾期合规</div><div class="kpi-value danger">{{ summary.overdue }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">运行 / 维修保养</div><div class="kpi-value">{{ summary.running }} / {{ summary.maintenance }}</div></div></el-col>
      </el-row>

      <el-tabs v-model="tab" class="mt8">
        <el-tab-pane label="设备台账" name="equipment">
          <div class="filters">
            <el-input v-model="equipmentQuery.keyword" placeholder="设备名称" size="small" clearable style="width: 150px" @keyup.enter="searchEquipment" />
            <el-select v-model="equipmentQuery.category" placeholder="等级" size="small" clearable style="width: 110px">
              <el-option label="A类关键设备" value="A" /><el-option label="B类重要设备" value="B" /><el-option label="C类一般设备" value="C" />
            </el-select>
            <el-select v-model="equipmentQuery.status" placeholder="状态" size="small" clearable style="width: 120px">
              <el-option label="运行" value="running" /><el-option label="维修保养" value="maintenance" /><el-option label="报废" value="scrapped" />
            </el-select>
            <el-button size="small" type="primary" @click="searchEquipment">筛选</el-button>
            <el-button size="small" type="success" @click="openEquipment()">新建设备</el-button>
          </div>
          <el-table :data="equipment" size="small" border>
            <el-table-column prop="code" label="设备编号" width="120" />
            <el-table-column prop="name" label="设备名称" min-width="160" />
            <el-table-column prop="category_label" label="等级" width="120" />
            <el-table-column prop="location" label="位置" min-width="140" />
            <el-table-column prop="install_date" label="安装日期" width="110" />
            <el-table-column prop="status_label" label="状态" width="100" />
            <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openEquipment(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeEquipment(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination class="pager" background layout="total, prev, pager, next" :total="equipmentTotal" :page-size="equipmentQuery.page_size" :current-page="equipmentQuery.page" @current-change="changeEquipmentPage" />
        </el-tab-pane>

        <el-tab-pane label="合规到期" name="compliance">
          <div class="filters">
            <el-date-picker v-model="complianceQuery.as_of" type="date" value-format="YYYY-MM-DD" size="small" style="width: 140px" @change="loadAll" />
            <el-select v-model="complianceQuery.kind" placeholder="类型" size="small" clearable style="width: 120px">
              <el-option label="年检" value="annual_inspection" /><el-option label="消防检查" value="fire" /><el-option label="维保" value="maintenance" /><el-option label="其他" value="other" />
            </el-select>
            <el-select v-model="complianceQuery.status" placeholder="状态" size="small" clearable style="width: 110px">
              <el-option label="待办" value="pending" /><el-option label="已完成" value="done" /><el-option label="已逾期" value="overdue" />
            </el-select>
            <el-select v-model="complianceQuery.equipment_id" placeholder="关联设备" size="small" clearable filterable style="width: 170px">
              <el-option v-for="item in equipmentOptions" :key="item.id" :label="`${item.code} · ${item.name}`" :value="item.id" />
            </el-select>
            <el-button size="small" type="primary" @click="searchCompliance">筛选</el-button>
            <el-button size="small" type="success" @click="openCompliance()">新增事项</el-button>
          </div>
          <el-table :data="compliance" size="small" border>
            <el-table-column prop="equipment_code" label="设备编号" width="115">
              <template #default="{ row }">{{ row.equipment_code || '楼宇级' }}</template>
            </el-table-column>
            <el-table-column prop="kind_label" label="类型" width="100" />
            <el-table-column prop="title" label="事项" min-width="170" show-overflow-tooltip />
            <el-table-column prop="due_date" label="到期日期" width="110" />
            <el-table-column prop="done_date" label="完成日期" width="110" />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="complianceTag(row.status)">{{ row.status_label }}</el-tag>
                <span v-if="row.status === 'overdue'" class="overdue-days">逾期 {{ row.overdue_days }} 天</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openCompliance(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeCompliance(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination class="pager" background layout="total, prev, pager, next" :total="complianceTotal" :page-size="complianceQuery.page_size" :current-page="complianceQuery.page" @current-change="changeCompliancePage" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="equipmentDialog" :title="equipmentForm.id ? '编辑设备' : '新建设备'" width="600px">
      <el-form :model="equipmentForm" label-width="95px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="设备编号" required><el-input v-model="equipmentForm.code" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="设备名称" required><el-input v-model="equipmentForm.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="等级"><el-select v-model="equipmentForm.category"><el-option label="A类关键设备" value="A" /><el-option label="B类重要设备" value="B" /><el-option label="C类一般设备" value="C" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="状态"><el-select v-model="equipmentForm.status"><el-option label="运行" value="running" /><el-option label="维修保养" value="maintenance" /><el-option label="报废" value="scrapped" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="位置"><el-input v-model="equipmentForm.location" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="安装日期"><el-date-picker v-model="equipmentForm.install_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="备注"><el-input v-model="equipmentForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="equipmentDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEquipment">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="complianceDialog" :title="complianceForm.id ? '编辑合规事项' : '新增合规事项'" width="600px">
      <el-form :model="complianceForm" label-width="95px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="关联设备"><el-select v-model="complianceForm.equipment_id" clearable filterable placeholder="可留空=楼宇级"><el-option v-for="item in equipmentOptions" :key="item.id" :label="`${item.code} · ${item.name}`" :value="item.id" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="类型"><el-select v-model="complianceForm.kind"><el-option label="年检" value="annual_inspection" /><el-option label="消防检查" value="fire" /><el-option label="维保" value="maintenance" /><el-option label="其他" value="other" /></el-select></el-form-item></el-col>
          <el-col :span="24"><el-form-item label="事项" required><el-input v-model="complianceForm.title" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="到期日期" required><el-date-picker v-model="complianceForm.due_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="完成日期"><el-date-picker v-model="complianceForm.done_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="备注"><el-input v-model="complianceForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="complianceDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveCompliance">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'

const loading = ref(false)
const saving = ref(false)
const tab = ref('equipment')
const today = new Date().toLocaleDateString('sv-SE')
const summary = ref({ equipment_count: 0, category_a: 0, compliance_30: 0, overdue: 0, running: 0, maintenance: 0 })
const equipment = ref([])
const equipmentTotal = ref(0)
const equipmentOptions = ref([])
const compliance = ref([])
const complianceTotal = ref(0)
const equipmentQuery = reactive({ keyword: '', category: '', status: '', page: 1, page_size: 20 })
const complianceQuery = reactive({ as_of: today, kind: '', status: '', equipment_id: null, page: 1, page_size: 20 })

const equipmentDialog = ref(false)
const equipmentForm = reactive({
  id: null, code: '', name: '', category: 'C', location: '',
  install_date: '', status: 'running', note: '',
})
const complianceDialog = ref(false)
const complianceForm = reactive({
  id: null, equipment_id: null, kind: 'other', title: '',
  due_date: '', done_date: '', note: '',
})

function complianceTag(status) {
  return { pending: 'warning', done: 'success', overdue: 'danger' }[status] || 'info'
}

async function loadSummary() {
  try {
    const { data } = await api.get('/business/facility/summary', { params: { as_of: complianceQuery.as_of } })
    summary.value = data
  } catch { /* 拦截器已提示 */ }
}

async function loadEquipment() {
  try {
    const { data } = await api.get('/business/facility/equipment', { params: { ...equipmentQuery } })
    equipment.value = data.items || []
    equipmentTotal.value = data.total ?? 0
  } catch { /* 拦截器已提示 */ }
}

async function loadCompliance() {
  try {
    const { data } = await api.get('/business/facility/compliance', { params: { ...complianceQuery } })
    compliance.value = data.items || []
    complianceTotal.value = data.total ?? 0
  } catch { /* 拦截器已提示 */ }
}

async function loadEquipmentOptions() {
  try {
    const { data } = await api.get('/business/facility/equipment', { params: { page: 1, page_size: 200 } })
    equipmentOptions.value = data.items || []
  } catch { /* 拦截器已提示 */ }
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadSummary(), loadEquipment(), loadCompliance(), loadEquipmentOptions()])
  } finally {
    loading.value = false
  }
}

function searchEquipment() {
  equipmentQuery.page = 1
  loadEquipment()
}

function searchCompliance() {
  complianceQuery.page = 1
  loadAll()
}

async function changeEquipmentPage(page) {
  equipmentQuery.page = page
  await loadEquipment()
}

async function changeCompliancePage(page) {
  complianceQuery.page = page
  await loadCompliance()
}

function openEquipment(row) {
  Object.assign(equipmentForm, row ? {
    id: row.id, code: row.code, name: row.name, category: row.category,
    location: row.location, install_date: row.install_date || '', status: row.status, note: row.note,
  } : {
    id: null, code: '', name: '', category: 'C', location: '',
    install_date: '', status: 'running', note: '',
  })
  equipmentDialog.value = true
}

async function saveEquipment() {
  if (saving.value) return
  if (!equipmentForm.code.trim() || !equipmentForm.name.trim()) {
    ElMessage.warning('设备编号和名称不能为空')
    return
  }
  saving.value = true
  try {
    const payload = { ...equipmentForm }
    delete payload.id
    if (equipmentForm.id) {
      await api.put(`/business/facility/equipment/${equipmentForm.id}`, payload)
    } else {
      await api.post('/business/facility/equipment', payload)
    }
    equipmentDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function removeEquipment(row) {
  try {
    await ElMessageBox.confirm(`确认删除设备「${row.code}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/facility/equipment/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

function openCompliance(row) {
  Object.assign(complianceForm, row ? {
    id: row.id, equipment_id: row.equipment_id ?? null, kind: row.kind, title: row.title,
    due_date: row.due_date || '', done_date: row.done_date || '', note: row.note,
  } : {
    id: null, equipment_id: null, kind: 'other', title: '',
    due_date: '', done_date: '', note: '',
  })
  complianceDialog.value = true
}

async function saveCompliance() {
  if (saving.value) return
  if (!complianceForm.title.trim() || !complianceForm.due_date) {
    ElMessage.warning('事项和到期日期不能为空')
    return
  }
  saving.value = true
  try {
    const payload = { ...complianceForm, done_date: complianceForm.done_date || null }
    delete payload.id
    if (complianceForm.id) {
      await api.put(`/business/facility/compliance/${complianceForm.id}`, payload)
    } else {
      await api.post('/business/facility/compliance', payload)
    }
    complianceDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function removeCompliance(row) {
  try {
    await ElMessageBox.confirm(`确认删除合规事项「${row.title}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/facility/compliance/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

function complianceExportUrl() {
  const params = new URLSearchParams()
  for (const key of ['kind', 'status', 'as_of', 'equipment_id']) {
    if (complianceQuery[key]) params.append(key, complianceQuery[key])
  }
  return `${api.defaults.baseURL}/business/facility/export/compliance.xlsx?${params.toString()}`
}

function exportEquipment() {
  window.location.href = `${api.defaults.baseURL}/business/facility/export/equipment.xlsx`
}

function exportCompliance() {
  window.location.href = complianceExportUrl()
}

onMounted(loadAll)
</script>

<style scoped>
.card-head { display: flex; justify-content: space-between; align-items: center; }
.kpi { margin-bottom: 8px; }
.kpi-box { background: #f5f7fa; border-radius: 6px; padding: 12px; }
.kpi-label { color: #909399; font-size: 12px; }
.kpi-value { font-size: 21px; font-weight: 600; margin-top: 5px; }
.kpi-value.danger { color: #f56c6c; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.mt8 { margin-top: 8px; }
.pager { margin-top: 10px; justify-content: flex-end; }
.overdue-days { color: #f56c6c; font-size: 12px; margin-left: 4px; }
</style>
