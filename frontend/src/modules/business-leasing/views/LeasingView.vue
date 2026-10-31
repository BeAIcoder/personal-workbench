<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>招商管理</span>
          <div>
            <el-button size="small" @click="loadAll">刷新</el-button>
            <el-button size="small" @click="exportFile('units.xlsx', '招商铺位台账.xlsx')">导出铺位</el-button>
            <el-button size="small" @click="exportFile('leads.xlsx', '招商线索.xlsx')">导出线索</el-button>
          </div>
        </div>
      </template>

      <el-row :gutter="12" class="kpi">
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">铺位总数</div><div class="kpi-value">{{ summary.total_units }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">总面积（㎡）</div><div class="kpi-value">{{ fmt(summary.total_area) }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">空置率</div><div class="kpi-value">{{ pct(summary.vacancy_rate) }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">在谈线索 / 到期跟进</div><div class="kpi-value">{{ funnel.active }} / {{ due.length }}</div></div></el-col>
      </el-row>
      <el-row :gutter="12" class="kpi">
        <el-col :span="8"><div class="kpi-box"><div class="kpi-label">出租率（已定+已租）</div><div class="kpi-value">{{ pct(summary.leasing_rate) }}</div></div></el-col>
        <el-col :span="8"><div class="kpi-box"><div class="kpi-label">开业率（截至今天）</div><div class="kpi-value">{{ pct(summary.opening_rate) }}</div></div></el-col>
        <el-col :span="8"><div class="kpi-box"><div class="kpi-label">在谈面积（在谈+已定）</div><div class="kpi-value">{{ fmt(summary.pipeline_area) }} ㎡</div></div></el-col>
      </el-row>
      <p class="hint">{{ summary.rate_note }}</p>

      <el-tabs v-model="tab" class="mt8">
        <el-tab-pane label="铺位台账" name="units">
          <div class="filters">
            <el-input v-model="unitQuery.keyword" placeholder="铺位号关键词" size="small" clearable style="width: 160px" @keyup.enter="searchUnits" />
            <el-select v-model="unitQuery.building" placeholder="楼栋" size="small" clearable style="width: 140px">
              <el-option v-for="b in buildings" :key="b" :label="b" :value="b" />
            </el-select>
            <el-select v-model="unitQuery.status" placeholder="状态" size="small" clearable style="width: 120px">
              <el-option v-for="s in unitStatuses" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
            <el-button size="small" type="primary" @click="searchUnits">筛选</el-button>
            <el-button size="small" type="success" @click="openUnit()">新建铺位</el-button>
          </div>
          <el-table :data="units" size="small" border>
            <el-table-column prop="building" label="楼栋" width="120" />
            <el-table-column prop="floor" label="楼层" width="80" />
            <el-table-column prop="unit_no" label="铺位号" width="100" />
            <el-table-column label="面积(㎡)" width="100" align="right">
              <template #default="{ row }">{{ fmt(row.area) }}</template>
            </el-table-column>
            <el-table-column label="租金单价" width="110" align="right">
              <template #default="{ row }">{{ fmt(row.rent_price) }}</template>
            </el-table-column>
            <el-table-column label="月租金" width="120" align="right">
              <template #default="{ row }">{{ fmt(row.monthly_rent) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="unitTag(row.status)">{{ row.status_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="opened_at" label="开业日期" width="110" />
            <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openUnit(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeUnit(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            class="pager"
            background
            layout="total, prev, pager, next"
            :total="unitTotal"
            :page-size="unitQuery.page_size"
            :current-page="unitQuery.page"
            @current-change="changeUnitPage"
          />
        </el-tab-pane>

        <el-tab-pane label="招商线索" name="leads">
          <div class="filters">
            <el-input v-model="leadQuery.keyword" placeholder="客户公司关键词" size="small" clearable style="width: 180px" @keyup.enter="searchLeads" />
            <el-select v-model="leadQuery.stage" placeholder="阶段" size="small" clearable style="width: 120px">
              <el-option v-for="s in leadStages" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
            <el-input v-model="leadQuery.owner" placeholder="负责人" size="small" clearable style="width: 120px" />
            <el-button size="small" type="primary" @click="searchLeads">筛选</el-button>
            <el-button size="small" type="success" @click="openLead()">新建线索</el-button>
          </div>
          <el-table :data="leads" size="small" border>
            <el-table-column prop="company" label="客户公司" min-width="160" show-overflow-tooltip />
            <el-table-column prop="contact" label="联系人" width="90" />
            <el-table-column prop="phone" label="电话" width="120" />
            <el-table-column label="需求面积" width="100" align="right">
              <template #default="{ row }">{{ fmt(row.demand_area) }}</template>
            </el-table-column>
            <el-table-column label="预算(元/月)" width="120" align="right">
              <template #default="{ row }">{{ fmt(row.budget) }}</template>
            </el-table-column>
            <el-table-column label="阶段" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="leadTag(row.stage)">{{ row.stage_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="意向铺位" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ unitLabel(row) }}</template>
            </el-table-column>
            <el-table-column prop="rent_form_label" label="租金形式" width="110" />
            <el-table-column prop="owner" label="负责人" width="90" />
            <el-table-column prop="next_action_at" label="下次跟进" width="110" />
            <el-table-column label="操作" width="190">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openLead(row)">编辑</el-button>
                <el-button size="small" text @click="openFollowups(row)">跟进</el-button>
                <el-button size="small" text type="danger" @click="removeLead(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            class="pager"
            background
            layout="total, prev, pager, next"
            :total="leadTotal"
            :page-size="leadQuery.page_size"
            :current-page="leadQuery.page"
            @current-change="changeLeadPage"
          />
        </el-tab-pane>

        <el-tab-pane label="漏斗与到期" name="funnel">
          <el-table :data="funnel.stages" size="small" border>
            <el-table-column prop="label" label="阶段" width="120" />
            <el-table-column prop="count" label="数量" width="100" />
            <el-table-column label="需求面积合计" width="140" align="right">
              <template #default="{ row }">{{ fmt(row.area) }}</template>
            </el-table-column>
            <el-table-column label="预算合计" width="140" align="right">
              <template #default="{ row }">{{ fmt(row.budget) }}</template>
            </el-table-column>
          </el-table>
          <p class="hint">线索总数 {{ funnel.total }}，在谈 {{ funnel.active }}，已签约 {{ funnel.signed }}，转化率 {{ pct(funnel.conversion_rate) }}。</p>
          <el-divider>到期跟进（下次跟进日期 ≤ 今天）</el-divider>
          <el-table :data="due" size="small" border>
            <el-table-column prop="company" label="客户公司" min-width="160" />
            <el-table-column prop="stage_label" label="阶段" width="100" />
            <el-table-column prop="owner" label="负责人" width="100" />
            <el-table-column prop="next_action_at" label="应跟进日期" width="120" />
            <el-table-column prop="phone" label="电话" width="130" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 铺位表单 -->
    <el-dialog v-model="unitDialog" :title="unitForm.id ? '编辑铺位' : '新建铺位'" width="560px">
      <el-form :model="unitForm" label-width="100px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="楼栋" required><el-input v-model="unitForm.building" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="楼层"><el-input v-model="unitForm.floor" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="铺位号" required><el-input v-model="unitForm.unit_no" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="状态"><el-select v-model="unitForm.status"><el-option v-for="s in unitStatuses" :key="s.value" :label="s.label" :value="s.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="面积(㎡)"><el-input-number v-model="unitForm.area" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="租金单价"><el-input-number v-model="unitForm.rent_price" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="物业费单价"><el-input-number v-model="unitForm.property_fee" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="开业日期"><el-date-picker v-model="unitForm.opened_at" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="备注"><el-input v-model="unitForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="unitDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveUnit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 线索表单 -->
    <el-dialog v-model="leadDialog" :title="leadForm.id ? '编辑线索' : '新建线索'" width="760px">
      <el-form :model="leadForm" label-width="110px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="客户公司" required><el-input v-model="leadForm.company" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系人"><el-input v-model="leadForm.contact" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="电话"><el-input v-model="leadForm.phone" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="行业"><el-input v-model="leadForm.industry" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="需求面积"><el-input-number v-model="leadForm.demand_area" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="预算(元/月)"><el-input-number v-model="leadForm.budget" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="阶段"><el-select v-model="leadForm.stage"><el-option v-for="s in leadStages" :key="s.value" :label="s.label" :value="s.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="负责人"><el-input v-model="leadForm.owner" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="意向铺位" :required="leadForm.stage === 'signed'"><el-select v-model="leadForm.unit_id" clearable filterable style="width: 100%"><el-option v-for="u in unitOptions" :key="u.id" :label="unitOptionLabel(u)" :value="u.id" :disabled="u.status === 'leased' && u.id !== leadForm.unit_id" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="租金形式"><el-select v-model="leadForm.rent_form" clearable style="width: 100%"><el-option v-for="r in rentForms" :key="r.value" :label="r.label" :value="r.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="免租期(月)"><el-input-number v-model="leadForm.free_rent_months" :min="0" :max="120" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="租期(月)"><el-input-number v-model="leadForm.lease_term_months" :min="1" :max="600" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="年递增率(%)"><el-input-number v-model="leadForm.escalation_rate" :min="0" :precision="2" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="装修补贴(元)"><el-input-number v-model="leadForm.fitout_subsidy" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="下次跟进"><el-date-picker v-model="leadForm.next_action_at" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="备注"><el-input v-model="leadForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="leadDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveLead">保存</el-button>
      </template>
    </el-dialog>

    <!-- 跟进记录 -->
    <el-dialog v-model="followDialog" :title="`跟进记录 · ${followLead?.company || ''}`" width="640px">
      <el-form :model="followForm" label-width="80px" class="follow-form">
        <el-row :gutter="12">
          <el-col :span="8"><el-form-item label="日期"><el-date-picker v-model="followForm.at" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="渠道"><el-select v-model="followForm.channel"><el-option v-for="c in channels" :key="c" :label="c" :value="c" /></el-select></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="结果"><el-input v-model="followForm.result" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="内容" required><el-input v-model="followForm.content" type="textarea" :rows="2" /></el-form-item>
        <el-button size="small" type="primary" :loading="saving" @click="saveFollowup">添加跟进</el-button>
      </el-form>
      <el-table :data="followups" size="small" border class="mt8">
        <el-table-column prop="at" label="日期" width="110" />
        <el-table-column prop="channel" label="渠道" width="80" />
        <el-table-column prop="content" label="内容" min-width="220" show-overflow-tooltip />
        <el-table-column prop="result" label="结果" width="120" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'

const loading = ref(false)
const saving = ref(false)
const tab = ref('units')

const unitStatuses = [
  { value: 'vacant', label: '空置' },
  { value: 'negotiating', label: '在谈' },
  { value: 'reserved', label: '已定' },
  { value: 'leased', label: '已租' },
]
const leadStages = [
  { value: 'lead', label: '线索' },
  { value: 'contact', label: '已联系' },
  { value: 'offer', label: '已报价' },
  { value: 'intent', label: '意向' },
  { value: 'signed', label: '已签约' },
  { value: 'lost', label: '已流失' },
]
const rentForms = [
  { value: 'fixed', label: '固定租金' },
  { value: 'percentage', label: '抽成租金' },
  { value: 'guaranteed_percentage', label: '保底+抽成' },
]
const channels = ['电话', '微信', '面谈', '邮件', '其他']

const summary = ref({
  total_units: 0, total_area: 0, vacancy_rate: 0, leasing_rate: 0,
  opening_rate: 0, pipeline_area: 0, rate_note: '', by_status: [],
})
const funnel = ref({ total: 0, active: 0, signed: 0, conversion_rate: 0, stages: [] })
const due = ref([])
const units = ref([])
const leads = ref([])
const unitOptions = ref([])
const buildings = ref([])
const unitTotal = ref(0)
const leadTotal = ref(0)
const unitQuery = reactive({ keyword: '', building: '', status: '', page: 1, page_size: 20 })
const leadQuery = reactive({ keyword: '', stage: '', owner: '', page: 1, page_size: 20 })

const unitDialog = ref(false)
const unitForm = reactive({ id: null, building: '', floor: '', unit_no: '', area: 0, rent_price: 0, property_fee: 0, status: 'vacant', opened_at: null, note: '' })
const leadDialog = ref(false)
const leadForm = reactive({
  id: null, company: '', contact: '', phone: '', industry: '', demand_area: 0,
  budget: 0, stage: 'lead', unit_id: null, rent_form: null, free_rent_months: null,
  escalation_rate: null, fitout_subsidy: null, lease_term_months: null,
  owner: '', next_action_at: '', note: '',
})
const followDialog = ref(false)
const followLead = ref(null)
const followForm = reactive({ at: '', channel: '电话', content: '', result: '' })
const followups = ref([])

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}
function pct(v) {
  const n = Number(v)
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '—'
}
function unitTag(s) {
  return { vacant: 'warning', negotiating: 'primary', reserved: 'success', leased: 'info' }[s] || 'info'
}
function leadTag(s) {
  return { lead: 'info', contact: 'primary', offer: 'warning', intent: 'success', signed: 'success', lost: 'danger' }[s] || 'info'
}
function unitOptionLabel(unit) {
  return [unit.building, unit.floor, unit.unit_no, unit.status_label].filter(Boolean).join(' ')
}
function unitLabel(row) {
  if (!row.unit_id) return '—'
  const unit = unitOptions.value.find((item) => item.id === row.unit_id)
  return unit ? [unit.building, unit.floor, unit.unit_no].filter(Boolean).join(' ') : `#${row.unit_id}`
}

async function loadSummary() {
  try {
    const [s, f, d] = await Promise.all([
      api.get('/business/leasing/units/summary'),
      api.get('/business/leasing/funnel'),
      api.get('/business/leasing/leads/due'),
    ])
    summary.value = s.data
    funnel.value = f.data
    due.value = d.data || []
    buildings.value = s.data.buildings || []
  } catch { /* 拦截器已提示 */ }
}

async function loadUnits() {
  loading.value = true
  try {
    const { data } = await api.get('/business/leasing/units', { params: unitQuery })
    units.value = data.items || []
    unitTotal.value = data.total ?? 0
  } catch { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

async function changeUnitPage(page) {
  unitQuery.page = page
  await loadUnits()
}

function searchUnits() {
  unitQuery.page = 1
  return loadUnits()
}

async function loadUnitOptions() {
  try {
    const { data } = await api.get('/business/leasing/units', { params: { page: 1, page_size: 200 } })
    unitOptions.value = data.items || []
  } catch { /* 拦截器已提示 */ }
}

async function loadLeads() {
  loading.value = true
  try {
    const { data } = await api.get('/business/leasing/leads', { params: leadQuery })
    leads.value = data.items || []
    leadTotal.value = data.total ?? 0
  } catch { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

async function changeLeadPage(page) {
  leadQuery.page = page
  await loadLeads()
}

function searchLeads() {
  leadQuery.page = 1
  return loadLeads()
}

async function loadAll() {
  await loadSummary()
  await loadUnitOptions()
  await loadUnits()
  await loadLeads()
}

function openUnit(row) {
  Object.assign(unitForm, row ? {
    id: row.id, building: row.building, floor: row.floor, unit_no: row.unit_no,
    area: row.area, rent_price: row.rent_price, property_fee: row.property_fee,
    status: row.status, opened_at: row.opened_at || null, note: row.note,
  } : { id: null, building: '', floor: '', unit_no: '', area: 0, rent_price: 0, property_fee: 0, status: 'vacant', opened_at: null, note: '' })
  unitDialog.value = true
}

async function saveUnit() {
  if (saving.value) return
  saving.value = true
  try {
    if (!unitForm.building.trim() || !unitForm.unit_no.trim()) {
      ElMessage.warning('楼栋与铺位号不能为空')
      return
    }
    const payload = { ...unitForm, opened_at: unitForm.opened_at || null }
    delete payload.id
    if (unitForm.id) {
      await api.put(`/business/leasing/units/${unitForm.id}`, payload)
    } else {
      await api.post('/business/leasing/units', payload)
    }
    unitDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function removeUnit(row) {
  try {
    await ElMessageBox.confirm(`确认删除铺位「${row.building} ${row.unit_no}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/leasing/units/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

function openLead(row) {
  Object.assign(leadForm, row ? {
    id: row.id, company: row.company, contact: row.contact, phone: row.phone,
    industry: row.industry, demand_area: row.demand_area, budget: row.budget,
    stage: row.stage, unit_id: row.unit_id ?? null, rent_form: row.rent_form || null,
    free_rent_months: row.free_rent_months ?? null, escalation_rate: row.escalation_rate ?? null,
    fitout_subsidy: row.fitout_subsidy ?? null, lease_term_months: row.lease_term_months ?? null,
    owner: row.owner, next_action_at: row.next_action_at || '', note: row.note,
  } : {
    id: null, company: '', contact: '', phone: '', industry: '', demand_area: 0,
    budget: 0, stage: 'lead', unit_id: null, rent_form: null, free_rent_months: null,
    escalation_rate: null, fitout_subsidy: null, lease_term_months: null,
    owner: '', next_action_at: '', note: '',
  })
  leadDialog.value = true
}

async function saveLead() {
  if (saving.value) return
  saving.value = true
  try {
    if (!leadForm.company.trim()) {
      ElMessage.warning('客户公司名称不能为空')
      return
    }
    if (leadForm.stage === 'signed' && !leadForm.unit_id) {
      ElMessage.warning('已签约线索必须选择意向铺位')
      return
    }
    const payload = {
      ...leadForm,
      next_action_at: leadForm.next_action_at || null,
      unit_id: leadForm.unit_id || null,
      rent_form: leadForm.rent_form || null,
      free_rent_months: leadForm.free_rent_months ?? null,
      escalation_rate: leadForm.escalation_rate ?? null,
      fitout_subsidy: leadForm.fitout_subsidy ?? null,
      lease_term_months: leadForm.lease_term_months ?? null,
    }
    delete payload.id
    if (leadForm.id) {
      await api.put(`/business/leasing/leads/${leadForm.id}`, payload)
    } else {
      await api.post('/business/leasing/leads', payload)
    }
    leadDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function removeLead(row) {
  try {
    await ElMessageBox.confirm(`确认删除线索「${row.company}」及其跟进记录？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/leasing/leads/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

async function openFollowups(row) {
  followLead.value = row
  followForm.at = new Date().toLocaleDateString('sv-SE')
  followForm.channel = '电话'
  followForm.content = ''
  followForm.result = ''
  followDialog.value = true
  await loadFollowups(row.id)
}

async function loadFollowups(leadId) {
  try {
    const { data } = await api.get(`/business/leasing/leads/${leadId}/followups`)
    followups.value = data || []
  } catch { /* 拦截器已提示 */ }
}

async function saveFollowup() {
  if (saving.value) return
  if (!followForm.content.trim()) {
    ElMessage.warning('跟进内容不能为空')
    return
  }
  saving.value = true
  try {
    await api.post(`/business/leasing/leads/${followLead.value.id}/followups`, {
      ...followForm, at: followForm.at || null,
    })
    followForm.content = ''
    followForm.result = ''
    await loadFollowups(followLead.value.id)
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

function exportFile(name, label) {
  window.open(`${api.defaults.baseURL}/business/leasing/export/${name}`, '_blank')
  ElMessage.info(`正在导出${label}`)
}

onMounted(loadAll)
</script>

<style scoped>
.page { padding: 0; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.kpi { margin-bottom: 8px; }
.kpi-box { background: #f5f7fa; border-radius: 6px; padding: 10px 12px; }
.kpi-label { font-size: 12px; color: #909399; }
.kpi-value { font-size: 20px; font-weight: 600; color: #303133; margin-top: 4px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.pager { margin-top: 10px; justify-content: flex-end; }
.mt8 { margin-top: 8px; }
.hint { font-size: 12px; color: #909399; margin: 8px 0 0; }
.follow-form { margin-bottom: 8px; }
</style>
