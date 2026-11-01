<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>企划管理</span>
          <div>
            <el-select v-model="year" size="small" style="width: 110px" @change="changeYear">
              <el-option v-for="y in years" :key="y" :label="`${y} 年`" :value="y" />
            </el-select>
            <el-button size="small" @click="loadAll">刷新</el-button>
            <el-button size="small" @click="exportFile">导出活动</el-button>
            <AskAiButton :prompt="aiPrompt" />
          </div>
        </div>
      </template>

      <el-row :gutter="12" class="kpi">
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">年度活动数</div><div class="kpi-value">{{ summary.campaigns }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">预算 / 实际</div><div class="kpi-value">¥{{ fmt(summary.budget_total) }} / ¥{{ fmt(summary.actual_total) }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">超支场次</div><div class="kpi-value">{{ summary.over_budget_count }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">平均 ROI（统一口径）</div><div class="kpi-value">{{ summary.avg_roi === null ? '—' : summary.avg_roi }}</div><div class="kpi-note">手填 {{ summary.manual_roi_count || 0 }} 场，不参与平均</div></div></el-col>
      </el-row>
      <el-alert :title="roiMethodNote" type="info" :closable="false" show-icon />

      <el-tabs v-model="tab" class="mt8">
        <el-tab-pane label="活动台账" name="campaigns">
          <div class="filters">
            <el-input v-model="query.keyword" placeholder="活动名称" size="small" clearable style="width: 170px" @keyup.enter="searchCampaigns" />
            <el-select v-model="query.status" placeholder="状态" size="small" clearable style="width: 120px">
              <el-option v-for="s in statuses" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
            <el-select v-model="query.category" placeholder="类型" size="small" clearable style="width: 110px">
              <el-option v-for="c in categories" :key="c.value" :label="c.label" :value="c.value" />
            </el-select>
            <el-button size="small" type="primary" @click="searchCampaigns">筛选</el-button>
            <el-button size="small" type="success" @click="openCampaign()">新建活动</el-button>
          </div>
          <el-table :data="campaigns" size="small" border>
            <el-table-column prop="name" label="活动名称" min-width="150" show-overflow-tooltip />
            <el-table-column prop="category_label" label="类型" width="80" />
            <el-table-column label="档期" width="190"><template #default="{ row }">{{ row.start_date }} ~ {{ row.end_date }}</template></el-table-column>
            <el-table-column prop="owner" label="负责人" width="90" />
            <el-table-column label="预算" width="105" align="right"><template #default="{ row }">¥{{ fmt(row.budget_total) }}</template></el-table-column>
            <el-table-column label="实际" width="105" align="right">
              <template #default="{ row }">
                <span :class="{ danger: row.over_budget }">¥{{ fmt(row.actual_total) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="grade" label="等级" width="65"><template #default="{ row }">{{ row.grade || '—' }}</template></el-table-column>
            <el-table-column label="ROI / 来源" width="150" align="right">
              <template #default="{ row }">
                <div>{{ row.roi ?? '—' }} <span class="muted">{{ roiSourceLabel(row.roi_source) }}</span></div>
                <div v-if="row.roi_warning" class="danger">{{ row.roi_warning }}</div>
              </template>
            </el-table-column>
            <el-table-column label="目标" width="70"><template #default="{ row }">{{ targetLabel(row.target_met) }}</template></el-table-column>
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
          <el-pagination
            class="mt8" layout="total, prev, pager, next" :total="campaignTotal"
            :page-size="query.page_size" :current-page="query.page"
            @current-change="changeCampaignPage"
          />
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

        <el-divider>效果复盘与统一 ROI 口径</el-divider>
        <el-row :gutter="12">
          <el-col :span="8"><el-form-item label="基准销售"><el-input-number v-model="campaignForm.sales_baseline" :min="0" :precision="2" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="期间销售"><el-input-number v-model="campaignForm.sales_during" :min="0" :precision="2" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="商户分摊"><el-input-number v-model="campaignForm.merchant_share" :min="0" :precision="2" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="人工等级"><el-select v-model="campaignForm.grade" clearable placeholder="不自动定级"><el-option v-for="g in ['S', 'A', 'B', 'C']" :key="g" :label="g" :value="g" /></el-select></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="ROI 目标"><el-input-number v-model="campaignForm.roi_target" :controls="false" placeholder="留空不判定" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="实际投入"><el-input :model-value="fmt(actualSum)" disabled /></el-form-item></el-col>
        </el-row>
        <div class="sum-line">{{ roiMethodNote }}</div>
        <el-row :gutter="12" class="mt8">
          <el-col :span="8"><el-form-item label="客流提升%"><el-input-number v-model="campaignForm.metrics.footfall_lift" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="销售提升%"><el-input-number v-model="campaignForm.metrics.sales_lift" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="手填 ROI"><el-input-number v-model="campaignForm.metrics.roi" :controls="false" style="width: 100%" /></el-form-item></el-col>
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
import AskAiButton from '../../../components/assistant/AskAiButton.vue'

const aiPrompt = computed(() => `请分析企划活动（${year.value} 年度）：ROI 口径与达标情况、档期冲突、预算执行与供应商评价`)
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

const summary = ref({
  campaigns: 0, budget_total: 0, actual_total: 0, over_budget_count: 0,
  avg_roi: null, manual_roi_count: 0,
})
const campaigns = ref([])
const campaignTotal = ref(0)
const vendors = ref([])
const calendar = ref({ year: year.value, months: [] })
const query = reactive({ keyword: '', status: '', category: '', page: 1, page_size: 20 })
const vendorQuery = reactive({ keyword: '', service: '' })
const roiMethodNote = '统一 ROI = (活动期间销售 - 基准销售) / (实际投入 + 商户分摊)；三项输入齐全才计算；手填 ROI 不参与平均；未设 ROI 目标时不判定达成或等级'

const campaignDialog = ref(false)
const campaignForm = reactive({
  id: null, name: '', year: year.value, category: 'promo', status: 'planning',
  start_date: '', end_date: '', owner: '', channel: '', target: '',
  grade: null, sales_baseline: null, sales_during: null, merchant_share: null, roi_target: null,
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
function roiSourceLabel(source) {
  return { calculated: '统一计算', manual: '历史手填', none: '未计算' }[source] || '未计算'
}
function targetLabel(met) {
  return met === true ? '达成' : met === false ? '未达成' : '—'
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
    campaignTotal.value = data.total || 0
  } catch { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

async function searchCampaigns() {
  query.page = 1
  await loadCampaigns()
}

async function changeCampaignPage(page) {
  query.page = page
  await loadCampaigns()
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

async function changeYear() {
  query.page = 1
  await loadAll()
}

function openCampaign(row) {
  if (row) {
    Object.assign(campaignForm, {
      id: row.id, name: row.name, year: row.year, category: row.category, status: row.status,
      start_date: row.start_date, end_date: row.end_date, owner: row.owner, channel: row.channel,
      target: row.target,
      grade: row.grade ?? null,
      sales_baseline: row.sales_baseline ?? null,
      sales_during: row.sales_during ?? null,
      merchant_share: row.merchant_share ?? null,
      roi_target: row.roi_target ?? null,
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
      grade: null, sales_baseline: null, sales_during: null, merchant_share: null, roi_target: null,
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
  let allowConflict = false
  try {
    while (true) {
      const payload = { ...campaignForm }
      delete payload.id
      if (allowConflict) payload.allow_conflict = true
      try {
        if (campaignForm.id) {
          await api.put(`/business/planning/campaigns/${campaignForm.id}`, payload)
        } else {
          await api.post('/business/planning/campaigns', payload)
        }
        campaignDialog.value = false
        await loadAll()
        break
      } catch (error) {
        if (error?.response?.status !== 409 || allowConflict) break
        const detail = error?.response?.data?.detail || '活动档期冲突'
        try {
          await ElMessageBox.confirm(`${detail}。是否确认有意并行并继续保存？`, '档期冲突确认', {
            type: 'warning', confirmButtonText: '确认并行', cancelButtonText: '返回编辑',
          })
        } catch { break }
        allowConflict = true
      }
    }
  } finally {
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
.kpi { margin-bottom: 8px; }
.danger { color: var(--el-color-danger); font-weight: 600; }
.sum-line { margin-top: 8px; font-size: 13px; color: var(--el-text-color-regular); }
.cal-tag { margin: 2px 6px 2px 0; }
.muted { color: var(--el-text-color-placeholder); }
</style>
