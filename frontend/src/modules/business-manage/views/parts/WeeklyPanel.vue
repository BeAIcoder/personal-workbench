<template>
  <div class="manage-panel" v-loading="loading">
    <el-row :gutter="12" class="kpi-row">
      <el-col v-for="card in kpiCards" :key="card.key" :xs="12" :sm="8" :md="4">
        <div class="kpi-box">
          <div class="kpi-label">{{ card.label }}</div>
          <div class="kpi-value">{{ card.value }}</div>
          <div class="kpi-sub">{{ card.sub }}</div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="12" class="chart-row">
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>出租率 vs 竞品</span></template>
          <div ref="occupancyChart" class="chart" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>收缴漏斗（最新期间）</span></template>
          <div ref="funnelChart" class="chart" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>招商累计 vs 目标</span></template>
          <div ref="leasingChart" class="chart" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>{{ vacancyTitle }}</span></template>
          <div ref="vacancyChart" class="chart" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="table-card">
      <template #header>
        <div class="block-head">
          <span class="block-title">本周明细表</span>
          <span class="hint">仅展示当前批次已识别的小节；曲靖缺失的小节自动隐藏</span>
        </div>
      </template>
      <div v-if="tableGroups.length">
        <div v-for="group in tableGroups" :key="group.key" class="record-block">
          <div class="block-head">
            <span class="block-title">{{ group.label }}</span>
            <span class="hint">{{ group.rows.length }} 条</span>
          </div>
          <el-table :data="group.rows" size="small" border empty-text="暂无记录">
            <el-table-column
              v-for="field in group.fields"
              :key="field.name"
              :label="field.label || field.name"
              :min-width="fieldWidth(field)"
              show-overflow-tooltip
            >
              <template #default="{ row }">{{ displayCell(row, field) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </div>
      <el-empty
        v-else
        :description="reportId ? '当前批次未识别到明细表' : '请先导入或选择批次'"
        :image-size="64"
      />
    </el-card>

    <el-card v-if="fundRows.length" shadow="never" class="table-card">
      <template #header>
        <div class="block-head">
          <span class="block-title">维修基金使用情况</span>
          <span class="hint">按年度 × 类别汇总，单元格为「项数 / 金额（元）」；缺源显示「—」</span>
        </div>
      </template>
      <el-table :data="fundRows" size="small" border empty-text="暂无维修基金数据">
        <el-table-column prop="year" label="年度" width="88" />
        <el-table-column prop="category" label="类别" width="120" />
        <el-table-column v-for="stage in FUND_STAGES" :key="stage.key" :label="stage.label" min-width="130">
          <template #default="{ row }">{{ fundCell(row, stage.key) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="text-card">
      <template #header>
        <div class="block-head">
          <span class="block-title">长文本小节</span>
          <span class="hint">{{ reportId ? '按小节保存，仅提交当前小节' : '请先导入或选择批次' }}</span>
        </div>
      </template>
      <div v-if="textSections.length">
        <div v-for="section in textSections" :key="section.key" class="text-block">
          <div class="block-head">
            <span class="block-title">{{ section.label }}</span>
            <el-button
              size="small"
              type="primary"
              :disabled="!reportId"
              :loading="savingText === section.key"
              @click="saveText(section)"
            >保存</el-button>
          </div>
          <el-input
            :model-value="textDrafts[section.key] || ''"
            type="textarea"
            :rows="4"
            :disabled="!reportId"
            placeholder="暂无内容"
            @update:model-value="textDrafts[section.key] = $event"
          />
        </div>
      </div>
      <el-empty
        v-else
        :description="reportId ? '当前批次未识别到长文本小节' : '请先导入或选择批次'"
        :image-size="64"
      />
    </el-card>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import api from '../../../../api'
import { bindChartResize } from '../../../../utils/chartResize.js'
import {
  emptyOverview,
  findMetric,
  fmtMetric,
  fmtNumber,
  fmtRate,
  groupBySection,
  normalizeOverview,
  toNumber,
  uniqueValues,
} from '../../shared.js'
import {
  collectionFunnelOption,
  compositionOption,
  leasingOption,
  occupancyOption,
} from '../../chartOptions.js'

const props = defineProps({
  project: { type: String, required: true },
  meta: { type: Object, default: null },
  reportId: { type: [Number, String], default: null },
  refreshKey: { type: Number, default: 0 },
})

const SYSTEM_FIELDS = new Set(['id', 'section', 'sort', 'created_at', 'updated_at', 'report_id'])
const KPI_DEFS = [
  { key: 'occupancy', label: '出租率', queries: ['出租率', '出租'], sub: '期末值' },
  { key: 'opening', label: '开业率', queries: ['开业率'], sub: '期末值' },
  { key: 'vacancy', label: '空铺率', queries: ['空铺率', '空置率'], sub: '空铺面积 / 可出租面积' },
  { key: 'collection', label: '累计收缴率', queries: ['累计收缴率', '收缴率'], sub: '年初至今' },
  { key: 'weekly_received', label: '本周收回', queries: ['本周收回', '本周回款'], sub: '单位随导入文件' },
  { key: 'new_area', label: '新签面积', queries: ['年内新签面积', '新签品牌面积', '新签面积'], sub: '年内累计' },
]
const WEEKLY_TEXT_FALLBACK = [
  { key: 'gap_analysis', label: '缺口分析' },
  { key: 'key_matters', label: '重点事项' },
  { key: 'next_plan', label: '下周计划' },
  { key: 'quality', label: '品质改造' },
  { key: 'brand_pr', label: '宣发叙述' },
]

const overview = ref(emptyOverview())
const detail = ref(null)
const loading = ref(false)
const savingText = ref('')
const textDrafts = reactive({})
const textMeta = reactive({})

const reportMetrics = computed(() => (Array.isArray(detail.value?.metrics) ? detail.value.metrics : []))
const reportRecords = computed(() => (Array.isArray(detail.value?.records) ? detail.value.records : []))
const reportTexts = computed(() => (Array.isArray(detail.value?.texts) ? detail.value.texts : []))
const reportTextKeys = computed(() => new Set(reportTexts.value.map((row) => row.section).filter(Boolean)))
const hasReportContent = computed(() => {
  const current = detail.value
  if (!current) return false
  return reportRecords.value.length > 0 || reportTexts.value.length > 0 || reportMetrics.value.length > 0
})

const projectKpis = computed(() => {
  const kpis = overview.value.kpis || []
  if (!props.project) return kpis
  return kpis.filter((item) => !item.project || item.project === props.project)
})

const latestKpis = computed(() => {
  const list = projectKpis.value
  if (!list.length) return []
  const periods = uniqueValues(list.map((item) => item.period)).sort()
  const latest = periods[periods.length - 1]
  return latest ? list.filter((item) => item.period === latest) : list
})

function overviewMetric(def) {
  const row = findMetric(latestKpis.value, def.queries)
  if (!row) return null
  return {
    label: row.label,
    value_num: toNumber(row.value),
    value_text: typeof row.value === 'string' ? row.value : '',
    unit: row.unit,
  }
}

const kpiCards = computed(() => KPI_DEFS.map((def) => {
  const metric = findMetric(reportMetrics.value, def.queries) || overviewMetric(def)
  return {
    key: def.key,
    label: def.label,
    value: metric ? fmtMetric(metric) : '—',
    sub: def.sub,
  }
}))

function latestEntry(rows) {
  const list = (Array.isArray(rows) ? rows : []).filter(Boolean)
  if (!list.length) return null
  return list.reduce((latest, row) => (
    !latest || String(row.period || '') > String(latest.period || '') ? row : latest
  ), null)
}

const activeProject = computed(() => detail.value?.project || '')

const FUND_STAGES = [
  { key: 'annual', label: '年度（项 / 预算）' },
  { key: 'project', label: '立项（项 / 金额）' },
  { key: 'design', label: '设计合同（项 / 金额）' },
  { key: 'construction', label: '施工合同（项 / 金额）' },
  { key: 'ongoing', label: '施工中（项 / 金额）' },
  { key: 'settled', label: '竣工待结算（项 / 金额）' },
]

const fundRows = computed(() => {
  const rows = new Map()
  for (const metric of reportMetrics.value) {
    const key = String(metric?.metric_key || '')
    if (!key.startsWith('weekly.fund.')) continue
    // weekly.fund.<年度>.<类别>.<阶段>_<count|amount>
    const parts = key.split('.')
    if (parts.length < 5) continue
    const year = parts[2]
    const category = parts[3]
    const matched = parts.slice(4).join('.').match(/^(.+)_(count|amount)$/)
    if (!matched) continue
    const id = `${year}|${category}`
    if (!rows.has(id)) rows.set(id, { year, category })
    rows.get(id)[`${matched[1]}_${matched[2]}`] = metric.value_num
  }
  return [...rows.values()]
})

function fundCell(row, stage) {
  const count = toNumber(row[`${stage}_count`])
  const amount = toNumber(row[`${stage}_amount`])
  if (count === null && amount === null) return '—'
  const countText = count === null ? '—' : `${fmtNumber(count)} 项`
  const amountText = amount === null ? '—' : `${fmtNumber(amount)} 元`
  return `${countText} / ${amountText}`
}

function sameProject(rowProject) {
  if (!rowProject) return true
  if (activeProject.value) return rowProject === activeProject.value
  if (!props.project) return true
  return rowProject === props.project || rowProject.includes(props.project.replace('项目', ''))
}

const occupancyRows = computed(() => {
  const competitor = reportMetrics.value
    .filter((row) => String(row?.metric_key || '').startsWith('weekly.competitor.occupancy_rate.'))
    .map((row) => ({
      period: '最新',
      project: String(row.metric_key).endsWith('.self') ? '我项目' : '竞品',
      rate: toNumber(row.value_num),
    }))
    .filter((row) => row.rate !== null)
  if (competitor.length) return competitor
  const trend = (overview.value.series.occupancy_trend || []).filter((row) => sameProject(row.project))
  const compare = overview.value.series.project_compare || []
  if (compare.length) {
    return compare.map((row) => ({
      period: '最新',
      project: row.project || props.project,
      rate: row.occupancy_rate,
    }))
  }
  const latest = latestEntry(trend)
  return latest
    ? [{ period: latest.period || '最新', project: latest.project || props.project, rate: latest.rate }]
    : []
})

const collectionRows = computed(() => (overview.value.series.collection_trend || [])
  .filter((row) => sameProject(row.project)))

const leasingRows = computed(() => (overview.value.series.leasing_progress || [])
  .filter((row) => sameProject(row.project)))

const vacancySplit = computed(() => {
  const vacant = findMetric(reportMetrics.value, ['空铺面积'])
  const rentable = findMetric(reportMetrics.value, ['可出租面积', '总租赁面积', '租赁面积'])
  const vacantValue = toNumber(vacant?.value_num)
  const rentableValue = toNumber(rentable?.value_num)
  if (vacantValue === null || rentableValue === null || rentableValue < vacantValue) return null
  return [
    { name: '空铺面积', value: vacantValue },
    { name: '已租面积', value: rentableValue - vacantValue },
  ]
})

const vacancyRows = computed(() => {
  if (vacancySplit.value) return vacancySplit.value
  return (overview.value.series.warning_reasons || []).map((row) => ({
    name: row.reason || '未标注',
    value: row.count,
  }))
})

const vacancyTitle = computed(() => (vacancySplit.value ? '空铺构成' : '预警原因构成'))

const metaRecordSections = computed(() => (
  Array.isArray(props.meta?.sections?.weekly?.records) ? props.meta.sections.weekly.records : []
))

function inferFields(rows) {
  const first = rows?.[0]
  if (!first) return []
  return Object.keys(first)
    .filter((key) => !SYSTEM_FIELDS.has(key))
    .map((key) => ({ name: key, label: key, type: 'text' }))
}

function fieldsOf(section, rows) {
  const declared = Array.isArray(section?.fields) ? section.fields : []
  if (declared.length) {
    return declared
      .map((field) => (typeof field === 'string' ? { name: field, label: field, type: 'text' } : field))
      .filter((field) => field?.name)
  }
  return inferFields(rows)
}

const tableGroups = computed(() => {
  const records = reportRecords.value
  const declared = metaRecordSections.value
  const declaredKeys = new Set(declared.map((section) => section.key))
  const groups = []
  for (const section of declared) {
    const rows = records.filter((row) => row?.section === section.key)
    if (!rows.length) continue
    groups.push({
      key: section.key,
      label: section.label || section.key,
      fields: fieldsOf(section, rows),
      rows,
    })
  }
  for (const [sectionKey, rows] of groupBySection(records)) {
    if (declaredKeys.has(sectionKey) || !rows.length) continue
    groups.push({
      key: sectionKey || 'unknown',
      label: sectionKey || '未分节',
      fields: inferFields(rows),
      rows,
    })
  }
  const rank = (group) => {
    if (/预警/.test(group.label)) return 0
    if (/宣发/.test(group.label)) return 1
    if (/维修|基金|品质/.test(group.label)) return 2
    return 9
  }
  return groups.sort((a, b) => rank(a) - rank(b))
})

const metaTexts = computed(() => (
  Array.isArray(props.meta?.sections?.weekly?.texts) ? props.meta.sections.weekly.texts : []
))

const textSections = computed(() => {
  if (metaTexts.value.length) {
    if (!detail.value || !hasReportContent.value) return metaTexts.value
    const visible = metaTexts.value.filter((section) => reportTextKeys.value.has(section.key))
    if (visible.length) return visible
    return [...reportTextKeys.value].map((key) => {
      const declared = metaTexts.value.find((section) => section.key === key)
      return declared || { key, label: key }
    })
  }
  if (reportTextKeys.value.size) {
    return [...reportTextKeys.value].map((key) => ({ key, label: key }))
  }
  return WEEKLY_TEXT_FALLBACK
})

function isLongField(field) {
  if (!field) return false
  if (field.type === 'textarea') return true
  const label = String(field.label || '')
  const name = String(field.name || '')
  return /内容|说明|备注|措施|进展|策略|情况|描述|计划|分析/.test(label)
    || /content|note|desc|remark|progress|plan|analysis/i.test(name)
}

function fieldWidth(field) {
  if (isLongField(field)) return 220
  if (field.type === 'date') return 112
  if (field.type === 'number') return 104
  return 140
}

function displayCell(row, field) {
  const value = row?.[field?.name]
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  const label = String(field?.label || '')
  if (field?.type === 'number') return fmtNumber(value)
  if (/率/.test(label) && toNumber(value) !== null) return fmtRate(value)
  if (/金额|面积|数量|费用|款|单价/.test(label) && toNumber(value) !== null) return fmtNumber(value)
  if (field?.type === 'date') return String(value).slice(0, 10)
  return String(value)
}

function syncTextDrafts() {
  for (const key of Object.keys(textDrafts)) delete textDrafts[key]
  for (const key of Object.keys(textMeta)) delete textMeta[key]
  for (const row of reportTexts.value) {
    const key = row?.section
    if (!key || key in textDrafts) continue
    textDrafts[key] = row.content || ''
    textMeta[key] = { subsection: row.subsection || '', sort: row.sort ?? 0 }
  }
}

async function saveText(section) {
  if (!props.reportId || savingText.value) return
  savingText.value = section.key
  try {
    const meta = textMeta[section.key] || {}
    await api.put(`/business/manage/reports/${props.reportId}/texts`, {
      items: [{
        section: section.key,
        subsection: meta.subsection || '',
        content: textDrafts[section.key] || '',
        sort: meta.sort ?? 0,
      }],
    })
    await loadDetail()
  } catch {
    /* 拦截器已提示 */
  } finally {
    savingText.value = ''
  }
}

const occupancyChart = ref(null)
const funnelChart = ref(null)
const leasingChart = ref(null)
const vacancyChart = ref(null)
let chartInstances = {}
let detachResize = () => {}

function renderCharts() {
  const options = {
    occupancy: occupancyOption(occupancyRows.value),
    funnel: collectionFunnelOption(collectionRows.value),
    leasing: leasingOption(leasingRows.value),
    vacancy: compositionOption(vacancyRows.value),
  }
  const refs = {
    occupancy: occupancyChart.value,
    funnel: funnelChart.value,
    leasing: leasingChart.value,
    vacancy: vacancyChart.value,
  }
  for (const [key, element] of Object.entries(refs)) {
    if (!element) continue
    chartInstances[key] ??= echarts.init(element)
    try {
      chartInstances[key].setOption(options[key], true)
    } catch (err) {
      // 单图配置异常不拖垮整页：记录带图表键的日志，其余图表继续渲染
      console.error('[business-manage] 图表渲染失败', key, err)
    }
  }
}

async function loadOverview() {
  try {
    const { data } = await api.get('/business/manage/overview', {
      params: { months: 12 },
    })
    overview.value = normalizeOverview(data)
  } catch {
    overview.value = emptyOverview()
  }
}

async function loadDetail() {
  if (!props.reportId) {
    detail.value = null
    syncTextDrafts()
    return
  }
  try {
    const { data } = await api.get(`/business/manage/reports/${props.reportId}`)
    detail.value = data || null
  } catch {
    detail.value = null
  }
  syncTextDrafts()
}

async function load() {
  loading.value = true
  try {
    await loadOverview()
    await loadDetail()
  } finally {
    loading.value = false
    await nextTick()
    renderCharts()
  }
}

watch(
  () => [props.reportId, props.project, props.refreshKey],
  () => load(),
)

onMounted(async () => {
  detachResize = bindChartResize(() => Object.values(chartInstances))
  await load()
})

onBeforeUnmount(() => {
  detachResize()
  for (const instance of Object.values(chartInstances)) instance?.dispose?.()
  chartInstances = {}
})
</script>

<style scoped>
.manage-panel {
  min-height: 200px;
}

.kpi-row {
  margin-bottom: 12px;
}

.chart-row {
  margin-bottom: 12px;
}

.chart-card {
  margin-bottom: 12px;
}

.chart {
  height: 260px;
}

.table-card,
.text-card {
  margin-bottom: 12px;
}

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.block-title {
  font-weight: 600;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.record-block {
  margin-bottom: 14px;
}

.record-block .block-head,
.text-block .block-head {
  margin: 10px 0 8px;
}

.text-block + .text-block {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
</style>
