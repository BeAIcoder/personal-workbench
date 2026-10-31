<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>企划管理</span>
          <div>
            <el-select v-model="year" size="small" style="width: 110px" @change="loadAll">
              <el-option v-for="y in years" :key="y" :label="`${y} 年`" :value="y" />
            </el-select>
            <el-button size="small" @click="loadAll">刷新</el-button>
            <el-button size="small" @click="exportFile">导出活动</el-button>
          </div>
        </div>
      </template>

      <el-row :gutter="12" class="kpi">
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">年度活动数</div><div class="kpi-value">{{ summary.campaigns }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">预算 / 实际</div><div class="kpi-value">¥{{ fmt(summary.budget_total) }} / ¥{{ fmt(summary.actual_total) }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">超支场次</div><div class="kpi-value">{{ summary.over_budget_count }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">平均 ROI</div><div class="kpi-value">{{ summary.avg_roi === null ? '—' : summary.avg_roi }}</div></div></el-col>
      </el-row>

      <el-tabs v-model="tab" class="mt8">
        <el-tab-pane label="活动台账" name="campaigns">
          <div class="filters">
            <el-input v-model="query.keyword" placeholder="活动名称" size="small" clearable style="width: 170px" @keyup.enter="loadCampaigns" />
            <el-select v-model="query.status" placeholder="状态" size="small" clearable style="width: 120px">
              <el-option v-for="s in statuses" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
            <el-select v-model="query.category" placeholder="类型" size="small" clearable style="width: 110px">
              <el-option v-for="c in categories" :key="c.value" :label="c.label" :value="c.value" />
            </el-select>
            <el-button size="small" type="primary" @click="loadCampaigns">筛选</el-button>
            <el-button size="small" type="success" @click="openCampaign()">新建活动</el-button>
          </div>
          <el-table :data="campaigns" size="small" border>
            <el-table-column prop="name" label="活动名称" min-width="170" show-overflow-tooltip />
            <el-table-column prop="category_label" label="类型" width="80" />
            <el-table-column label="档期" width="190"><template #default="{ row }">{{ row.start_date }} ~ {{ row.end_date }}</template></el-table-column>
            <el-table-column prop="owner" label="负责人" width="90" />
            <el-table-column label="预算" width="110" align="right"><template #default="{ row }">¥{{ fmt(row.budget_total) }}</template></el-table-column>
            <el-table-column label="实际" width="110" align="right">
              <template #default="{ row }">
                <span :class="{ danger: row.over_budget }">¥{{ fmt(row.actual_total) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="ROI" width="90" align="right"><template #default="{ row }">{{ row.roi ?? '—' }}</template></el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag size="small" :type="statusTag(row.status)">{{ row.status_label }}</el-tag></template>
            </el-table-column>
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openCampaign(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeCampaign(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="供应商" name="vendors">
          <div class="filters">
            <el-input v-model="vendorQuery.keyword" placeholder="供应商名称" size="small" clearable style="width: 170px" />
            <el-select v-model="vendorQuery.service" placeholder="服务类型" size="small" clearable style="width: 120px">
              <el-option v-for="s in vendorServices" :key="s" :label="s" :value="s" />
            </el-select>
            <el-button size="small" type="primary" @click="loadVendors">筛选</el-button>
            <el-button size="small" type="success" @click="openVendor()">新建供应商</el-button>
          </div>
          <el-table :data="vendors" size="small" border>
            <el-table-column prop="name" label="供应商" min-width="180" />
            <el-table-column prop="service" label="服务类型" width="100" />
            <el-table-column prop="contact" label="联系人" width="100" />
            <el-table-column prop="phone" label="电话" width="130" />
            <el-table-column label="评分" width="120"><template #default="{ row }"><el-rate :model-value="row.rating" disabled size="small" /></template></el-table-column>
            <el-table-column prop="note" label="备注" min-width="160" show-overflow-tooltip />
            <el-table-column label="操作" width="130">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openVendor(row)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeVendor(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="年度日历" name="calendar">
          <el-table :data="calendar.months" size="small" border>
            <el-table-column label="月份" width="90"><template #default="{ row }">{{ row.month }} 月</template></el-table-column>
            <el-table-column label="活动">
              <template #default="{ row }">
                <el-tag v-for="c in row.items" :key="c.id" size="small" class="cal-tag" :type="statusTag(c.status)">
                  {{ c.name }}（{{ c.start_date }}~{{ c.end_date }}）
                </el-tag>
                <span v-if="!row.items.length" class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="campaignDialog" :title="campaignForm.id ? '编辑活动' : '新建活动'" width="760px">
      <el-form :model="campaignForm" label-width="90px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="活动名称" required><el-input v-model="campaignForm.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="年度"><el-input-number v-model="campaignForm.year" :min="2000" :max="2100" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="类型"><el-select v-model="campaignForm.category"><el-option v-for="c in categories" :key="c.value" :label="c.label" :value="c.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="状态"><el-select v-model="campaignForm.status"><el-option v-for="s in statuses" :key="s.value" :label="s.label" :value="s.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="开始日期" required><el-date-picker v-model="campaignForm.start_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="结束日期" required><el-date-picker v-model="campaignForm.end_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="负责人"><el-input v-model="campaignForm.owner" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="渠道"><el-input v-model="campaignForm.channel" placeholder="公众号/场内/抖音…" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="活动目标"><el-input v-model="campaignForm.target" /></el-form-item>

        <el-divider>预算明细</el-divider>
        <el-table :data="campaignForm.budget_items" size="small" border>
          <el-table-column label="科目" min-width="180">
            <template #default="{ row }"><el-input v-model="row.item" size="small" /></template>
          </el-table-column>
          <el-table-column label="预算" width="150">
            <template #default="{ row }"><el-input-number v-model="row.budget" :min="0" :controls="false" size="small" style="width: 100%" /></template>
          </el-table-column>
          <el-table-column label="实际" width="150">
            <template #default="{ row }"><el-input-number v-model="row.actual" :min="0" :controls="false" size="small" style="width: 100%" /></template>
          </el-table-column>
          <el-table-column label="操作" width="80">
            <template #default="{ $index }"><el-button size="small" text type="danger" @click="campaignForm.budget_items.splice($index, 1)">删除</el-button></template>
          </el-table-column>
        </el-table>
        <el-button size="small" class="mt8" @click="campaignForm.budget_items.push({ item: '', budget: 0, actual: 0 })">+ 加一行</el-button>
        <div class="sum-line">预算合计 ¥{{ fmt(budgetSum) }}　实际合计 ¥{{ fmt(actualSum) }}</div>

        <el-divider>效果复盘</el-divider>
        <el-row :gutter="12">
          <el-col :span="8"><el-form-item label="客流提升%"><el-input-number v-model="campaignForm.metrics.footfall_lift" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="销售提升%"><el-input-number v-model="campaignForm.metrics.sales_lift" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="ROI"><el-input-number v-model="campaignForm.metrics.roi" :controls="false" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="复盘结论"><el-input v-model="campaignForm.metrics.review" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="campaignDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveCampaign">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="vendorDialog" :title="vendorForm.id ? '编辑供应商' : '新建供应商'" width="520px">
      <el-form :model="vendorForm" label-width="90px">
        <el-form-item label="供应商" required><el-input v-model="vendorForm.name" /></el-form-item>
        <el-form-item label="服务类型"><el-select v-model="vendorForm.service"><el-option v-for="s in vendorServices" :key="s" :label="s" :value="s" /></el-select></el-form-item>
        <el-form-item label="联系人"><el-input v-model="vendorForm.contact" /></el-form-item>
        <el-form-item label="电话"><el-input v-model="vendorForm.phone" /></el-form-item>
        <el-form-item label="评分"><el-rate v-model="vendorForm.rating" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="vendorForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="vendorDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveVendor">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'

const loading = ref(false)
const saving = ref(false)
const tab = ref('campaigns')
const year = ref(new Date().getFullYear())
const years = [year.value - 1, year.value, year.value + 1]

const statuses = [
  { value: 'planning', label: '策划中' },
  { value: 'approved', label: '已立项' },
  { value: 'running', label: '进行中' },
  { value: 'done', label: '已结束' },
  { value: 'cancelled', label: '已取消' },
]
const categories = [
  { value: 'festival', label: '节庆' },
  { value: 'promo', label: '促销' },
  { value: 'brand', label: '品牌' },
  { value: 'community', label: '社区' },
]
const vendorServices = ['物料', '美陈', '演艺', '投放', '执行']

const summary = ref({ campaigns: 0, budget_total: 0, actual_total: 0, over_budget_count: 0, avg_roi: null })
const campaigns = ref([])
const vendors = ref([])
const calendar = ref({ year: year.value, months: [] })
const query = reactive({ keyword: '', status: '', category: '' })
const vendorQuery = reactive({ keyword: '', service: '' })

const campaignDialog = ref(false)
const campaignForm = reactive({
  id: null, name: '', year: year.value, category: 'promo', status: 'planning',
  start_date: '', end_date: '', owner: '', channel: '', target: '',
  budget_items: [], metrics: { footfall_lift: null, sales_lift: null, roi: null, review: '' },
})
const vendorDialog = ref(false)
const vendorForm = reactive({ id: null, name: '', service: '执行', contact: '', phone: '', rating: 3, note: '' })

const budgetSum = computed(() => campaignForm.budget_items.reduce((s, i) => s + (Number(i.budget) || 0), 0))
const actualSum = computed(() => campaignForm.budget_items.reduce((s, i) => s + (Number(i.actual) || 0), 0))

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}
function statusTag(s) {
  return { planning: 'info', approved: 'primary', running: 'warning', done: 'success', cancelled: 'danger' }[s] || 'info'
}

async function loadSummary() {
  try {
    const { data } = await api.get('/business/planning/summary', { params: { year: year.value } })
    summary.value = data
  } catch { /* 拦截器已提示 */ }
}

async function loadCampaigns() {
  loading.value = true
  try {
    const { data } = await api.get('/business/planning/campaigns', { params: { ...query, year: year.value } })
    campaigns.value = data.items || []
  } catch { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

async function loadVendors() {
  try {
    const { data } = await api.get('/business/planning/vendors', { params: vendorQuery })
    vendors.value = data || []
  } catch { /* 拦截器已提示 */ }
}

async function loadCalendar() {
  try {
    const { data } = await api.get('/business/planning/campaigns/calendar', { params: { year: year.value } })
    calendar.value = data
  } catch { /* 拦截器已提示 */ }
}

async function loadAll() {
  await loadSummary()
  await Promise.all([loadCampaigns(), loadVendors(), loadCalendar()])
}

function openCampaign(row) {
  if (row) {
    Object.assign(campaignForm, {
      id: row.id, name: row.name, year: row.year, category: row.category, status: row.status,
      start_date: row.start_date, end_date: row.end_date, owner: row.owner, channel: row.channel,
      target: row.target,
      budget_items: (row.budget_items || []).map((i) => ({ ...i })),
      metrics: {
        footfall_lift: row.metrics?.footfall_lift ?? null,
        sales_lift: row.metrics?.sales_lift ?? null,
        roi: row.metrics?.roi ?? null,
        review: row.metrics?.review || '',
      },
    })
  } else {
    Object.assign(campaignForm, {
      id: null, name: '', year: year.value, category: 'promo', status: 'planning',
      start_date: '', end_date: '', owner: '', channel: '', target: '',
      budget_items: [{ item: '物料', budget: 0, actual: 0 }],
      metrics: { footfall_lift: null, sales_lift: null, roi: null, review: '' },
    })
  }
  campaignDialog.value = true
}

async function saveCampaign() {
  if (saving.value) return
  if (!campaignForm.name.trim()) {
    ElMessage.warning('活动名称不能为空')
    return
  }
  if (!campaignForm.start_date || !campaignForm.end_date) {
    ElMessage.warning('请填写活动档期')
    return
  }
  saving.value = true
  try {
    const payload = { ...campaignForm }
    delete payload.id
    if (campaignForm.id) {
      await api.put(`/business/planning/campaigns/${campaignForm.id}`, payload)
    } else {
      await api.post('/business/planning/campaigns', payload)
    }
    campaignDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function removeCampaign(row) {
  try {
    await ElMessageBox.confirm(`确认删除活动「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/planning/campaigns/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

function openVendor(row) {
  Object.assign(vendorForm, row ? { ...row } : { id: null, name: '', service: '执行', contact: '', phone: '', rating: 3, note: '' })
  vendorDialog.value = true
}

async function saveVendor() {
  if (saving.value) return
  if (!vendorForm.name.trim()) {
    ElMessage.warning('供应商名称不能为空')
    return
  }
  saving.value = true
  try {
    const payload = { ...vendorForm }
    delete payload.id
    if (vendorForm.id) {
      await api.put(`/business/planning/vendors/${vendorForm.id}`, payload)
    } else {
      await api.post('/business/planning/vendors', payload)
    }
    vendorDialog.value = false
    await loadVendors()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function removeVendor(row) {
  try {
    await ElMessageBox.confirm(`确认删除供应商「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/planning/vendors/${row.id}`)
    await loadVendors()
  } catch { /* 拦截器已提示 */ }
}

function exportFile() {
  window.open(`${api.defaults.baseURL}/business/planning/export/campaigns.xlsx?year=${year.value}`, '_blank')
  ElMessage.info('正在导出')
}

onMounted(loadAll)
</script>

<style scoped>
.page { padding: 0; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.kpi { margin-bottom: 8px; }
.kpi-box { background: #f5f7fa; border-radius: 6px; padding: 10px 12px; }
.kpi-label { font-size: 12px; color: #909399; }
.kpi-value { font-size: 20px; font-weight: 600; color: #303133; margin-top: 4px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.mt8 { margin-top: 8px; }
.danger { color: #f56c6c; font-weight: 600; }
.sum-line { margin-top: 8px; font-size: 13px; color: #606266; }
.cal-tag { margin: 2px 6px 2px 0; }
.muted { color: #c0c4cc; }
</style>
