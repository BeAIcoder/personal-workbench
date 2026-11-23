<template>
  <div class="manage-panel" v-loading="loading">
    <el-row :gutter="12" class="kpi-row">
      <el-col v-for="card in kpiCards" :key="card.key" :xs="12" :sm="12" :md="6">
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
          <template #header><span>责任书完成率</span></template>
          <div ref="kpiChart" class="chart" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>收入完成率（应收 / 已收）</span></template>
          <div ref="incomeChart" class="chart" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>招商完成率</span></template>
          <div ref="leasingChart" class="chart" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>诉讼阶段与金额 Top10</span></template>
          <div ref="litigationChart" class="chart" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="tabs-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane v-for="tab in controlTabs" :key="tab.name" :label="tab.label" :name="tab.name">
          <template v-if="tab.kind === 'records'">
            <div v-if="tab.sections.length">
              <div v-for="section in tab.sections" :key="section.key" class="record-block">
                <div class="block-head">
                  <span class="block-title">{{ section.label || section.key }}</span>
                  <el-button
                    size="small"
                    type="success"
                    :disabled="!reportId"
                    @click="openRecord(section)"
                  >新增</el-button>
                </div>
                <el-table :data="recordsOf(section.key)" size="small" border empty-text="暂无记录">
                  <el-table-column
                    v-for="field in fieldsOf(section)"
                    :key="field.name"
                    :label="field.label || field.name"
                    :min-width="fieldWidth(field)"
                    show-overflow-tooltip
                  >
                    <template #default="{ row }">{{ displayField(row, field) }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="120" fixed="right">
                    <template #default="{ row }">
                      <el-button size="small" text type="primary" @click="openRecord(section, row)">编辑</el-button>
                      <el-button size="small" text type="danger" @click="removeRecord(row)">删除</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>
            <el-empty
              v-else
              :description="reportId ? `当前批次未识别到「${tab.label}」表格` : '请先导入或选择批次'"
              :image-size="64"
            />
          </template>

          <template v-else>
            <div v-if="tab.metricGroups.length">
              <div v-for="group in tab.metricGroups" :key="group.key" class="metric-group">
                <div class="block-head">
                  <span class="block-title">{{ group.label }}</span>
                  <el-button
                    size="small"
                    type="primary"
                    :disabled="!reportId"
                    :loading="savingMetrics === group.key"
                    @click="saveMetrics(group)"
                  >保存</el-button>
                </div>
                <el-table :data="group.rows" size="small" border>
                  <el-table-column prop="label" label="指标" min-width="200" show-overflow-tooltip />
                  <el-table-column label="数值" width="200">
                    <template #default="{ row }">
                      <el-input-number
                        :model-value="metricDrafts[draftKey(row)]"
                        :controls="false"
                        :precision="2"
                        :disabled="!reportId"
                        style="width: 100%"
                        @update:model-value="metricDrafts[draftKey(row)] = $event"
                      />
                    </template>
                  </el-table-column>
                  <el-table-column prop="unit" label="单位" width="90" />
                  <el-table-column label="导入原文（可选）" min-width="220">
                    <template #default="{ row }">
                      <el-input
                        :model-value="metricTextDrafts[draftKey(row)]"
                        size="small"
                        :disabled="!reportId"
                        placeholder="保留原始口径说明"
                        @update:model-value="metricTextDrafts[draftKey(row)] = $event"
                      />
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>
            <el-empty
              v-else
              :description="reportId ? `当前批次未识别到「${tab.label}」指标` : '请先导入或选择批次'"
              :image-size="64"
            />
          </template>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog
      v-model="recordDialog"
      :title="recordEditingId ? '编辑记录' : '新增记录'"
      width="760px"
      destroy-on-close
    >
      <el-empty v-if="!recordFields.length" description="该表格未识别到可编辑字段" :image-size="60" />
      <el-form v-else :model="recordForm" label-width="110px">
        <el-row :gutter="12">
          <el-col v-for="field in recordFields" :key="field.name" :span="fieldSpan(field)">
            <el-form-item :label="field.label || field.name">
              <el-input-number
                v-if="field.type === 'number'"
                :model-value="recordForm[field.name]"
                :controls="false"
                style="width: 100%"
                @update:model-value="recordForm[field.name] = $event"
              />
              <el-date-picker
                v-else-if="field.type === 'date'"
                :model-value="recordForm[field.name]"
                type="date"
                value-format="YYYY-MM-DD"
                style="width: 100%"
                @update:model-value="recordForm[field.name] = $event"
              />
              <el-input
                v-else-if="isLongField(field)"
                :model-value="recordForm[field.name]"
                type="textarea"
                :rows="3"
                @update:model-value="recordForm[field.name] = $event"
              />
              <el-input
                v-else
                :model-value="recordForm[field.name]"
                @update:model-value="recordForm[field.name] = $event"
              />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="recordDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!recordFields.length" :loading="saving" @click="saveRecord">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import api from '../../../../api'
import { bindChartResize } from '../../../../utils/chartResize.js'
import {
  averageNumbers,
  emptyOverview,
  findMetric,
  fmtInt,
  fmtNumber,
  fmtRate,
  normalizeOverview,
  sumNumbers,
  toNumber,
} from '../../shared.js'
import {
  collectionTrendOption,
  kpiOption,
  leasingOption,
  litigationOption,
} from '../../chartOptions.js'

const props = defineProps({
  meta: { type: Object, default: null },
  reportId: { type: [Number, String], default: null },
  refreshKey: { type: Number, default: 0 },
})

const CONTROL_TABS = [
  { name: 'work', label: '重点工作', kind: 'records', patterns: ['重点', '工作'] },
  { name: 'responsibility', label: '责任书', kind: 'records', patterns: ['责任', '业绩'] },
  { name: 'income', label: '收入', kind: 'metrics', patterns: ['收入', '应收', '回款', '现流', '权责', '利润', '收缴'] },
  { name: 'leasing', label: '招商', kind: 'metrics', patterns: ['招商', '租赁', '签约', '新签', '续签'] },
  { name: 'litigation', label: '诉讼', kind: 'records', patterns: ['诉讼', '案件', '纠纷'] },
  { name: 'board', label: '总办会', kind: 'records', patterns: ['总办', '决策', '上会'] },
]
const SYSTEM_FIELDS = new Set(['id', 'section', 'sort', 'created_at', 'updated_at', 'report_id'])

const detail = ref(null)
const overview = ref(emptyOverview())
const loading = ref(false)
const activeTab = ref('work')
const saving = ref(false)
const savingMetrics = ref('')

const metaSections = computed(() => props.meta?.sections?.control || {})
const metaRecords = computed(() => (Array.isArray(metaSections.value.records) ? metaSections.value.records : []))
const metaMetrics = computed(() => (Array.isArray(metaSections.value.metrics) ? metaSections.value.metrics : []))
const reportRecords = computed(() => (Array.isArray(detail.value?.records) ? detail.value.records : []))
const reportMetrics = computed(() => (Array.isArray(detail.value?.metrics) ? detail.value.metrics : []))

function recordsOf(sectionKey) {
  if (!sectionKey) return []
  return reportRecords.value.filter((row) => row?.section === sectionKey)
}

const METRIC_SECTION_HINTS = {
  income: /income|revenue|collection|profit|receivable/i,
  leasing: /leasing|lease|sign(?:ed|ing)?|contract/i,
}

function matchMetric(tab, spec) {
  const text = [
    spec?.label || '',
    spec?.key || '',
    spec?.metric_key || '',
    spec?.section || '',
    existingMetric(spec)?.section || '',
  ].join(' ')
  if (tab.patterns.some((pattern) => text.includes(pattern))) return true
  const hint = METRIC_SECTION_HINTS[tab.name]
  return hint ? hint.test(text) : false
}

function existingMetric(spec) {
  const key = spec?.key || spec?.metric_key || ''
  const label = spec?.label || ''
  return reportMetrics.value.find((row) => (key && row.metric_key === key) || (label && row.label === label)) || null
}

const recordTabSections = computed(() => {
  const result = Object.fromEntries(CONTROL_TABS.filter((tab) => tab.kind === 'records').map((tab) => [tab.name, []]))
  const leftovers = []
  for (const section of metaRecords.value) {
    const text = `${section?.label || ''} ${section?.key || ''}`
    const tab = CONTROL_TABS.find((item) => item.kind === 'records' && item.patterns.some((p) => text.includes(p)))
    if (tab) result[tab.name].push(section)
    else leftovers.push(section)
  }
  // 未匹配到页签的表格归入「重点工作」，避免导入数据不可见
  result.work.push(...leftovers)
  return result
})

const metricGroups = computed(() => {
  const result = { income: [], leasing: [] }
  const specs = metaMetrics.value.length
    ? metaMetrics.value
    : reportMetrics.value.map((row) => ({
      key: row.metric_key,
      label: row.label,
      unit: row.unit,
      section: row.section,
    }))
  const used = new Set()
  for (const tab of CONTROL_TABS.filter((item) => item.kind === 'metrics')) {
    const groups = new Map()
    for (const spec of specs) {
      if (used.has(spec) || !matchMetric(tab, spec)) continue
      used.add(spec)
      const section = spec.section || existingMetric(spec)?.section || tab.name
      if (!groups.has(section)) groups.set(section, [])
      groups.get(section).push({
        section,
        key: spec.key || spec.metric_key || spec.label,
        label: spec.label || spec.key || spec.metric_key,
        unit: spec.unit || '',
        sort: groups.get(section).length,
      })
    }
    const entries = [...groups.entries()]
    result[tab.name] = entries.map(([section, rows]) => ({
      key: `${tab.name}:${section}`,
      section,
      label: entries.length > 1 ? `${tab.label} · ${section}` : `${tab.label}指标`,
      rows,
    }))
  }
  const leftovers = specs.filter((spec) => !used.has(spec))
  if (leftovers.length) {
    const section = leftovers[0]?.section || 'other'
    result.income.push({
      key: 'income:other',
      section,
      label: '其他经营指标',
      rows: leftovers.map((spec, index) => ({
        section: spec.section || section,
        key: spec.key || spec.metric_key || spec.label,
        label: spec.label || spec.key || spec.metric_key,
        unit: spec.unit || '',
        sort: index,
      })),
    })
  }
  return result
})

const controlTabs = computed(() => CONTROL_TABS.map((tab) => ({
  ...tab,
  sections: recordTabSections.value[tab.name] || [],
  metricGroups: metricGroups.value[tab.name] || [],
})))

function rateFromRow(row) {
  const rate = toNumber(row?.rate)
  if (rate !== null) return rate
  const target = toNumber(row?.target)
  const actual = toNumber(row?.actual)
  if (target !== null && target !== 0 && actual !== null) return actual / target
  return null
}

function latestRow(rows) {
  const list = (Array.isArray(rows) ? rows : []).filter(Boolean)
  if (!list.length) return null
  return list.reduce((latest, row) => (
    !latest || String(row.period || '') > String(latest.period || '') ? row : latest
  ), null)
}

const kpiCards = computed(() => {
  const completion = overview.value.series.kpi_completion || []
  const averageRate = averageNumbers(completion.map((row) => rateFromRow(row)))
  const incomeRow = findMetric(completion, ['营业总收入', '收入'], 'name')
  const incomeRate = incomeRow
    ? rateFromRow(incomeRow)
    : rateFromRow(latestRow(overview.value.series.collection_trend))
  const leasingRow = findMetric(completion, ['招商', '出租率'], 'name')
  const leasingRate = leasingRow
    ? rateFromRow(leasingRow)
    : rateFromRow(latestRow(overview.value.series.leasing_progress))
  const stages = overview.value.series.litigation_by_stage || []
  const litigationCount = sumNumbers(stages.map((row) => row?.count))
  const litigationAmount = sumNumbers(stages.map((row) => row?.amount))
  const workCount = (recordTabSections.value.work || [])
    .reduce((total, section) => total + recordsOf(section.key).length, 0)
  return [
    { key: 'work', label: '重点工作事项', value: workCount ? `${workCount} 项` : '—', sub: '本批次记录' },
    {
      key: 'kpi',
      label: '责任书平均完成率',
      value: fmtRate(averageRate),
      sub: completion.length ? `${completion.length} 项指标` : '暂无数据',
    },
    { key: 'income', label: '收入完成率', value: fmtRate(incomeRate), sub: '责任书 / 收缴口径' },
    {
      key: 'litigation',
      label: '在办诉讼',
      value: litigationCount === null ? '—' : `${fmtInt(litigationCount)} 件`,
      sub: litigationAmount === null ? '暂无金额' : `欠费合计 ${fmtNumber(litigationAmount)} 元`,
    },
  ]
})

function fieldsOf(section) {
  const declared = Array.isArray(section?.fields) ? section.fields : []
  if (declared.length) {
    return declared
      .map((field) => (typeof field === 'string' ? { name: field, label: field, type: 'text' } : field))
      .filter((field) => field?.name)
  }
  const first = recordsOf(section?.key)[0]
  if (!first) return []
  return Object.keys(first)
    .filter((key) => !SYSTEM_FIELDS.has(key))
    .map((key) => ({ name: key, label: key, type: 'text' }))
}

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
  if (field.type === 'number') return 110
  return 140
}

function fieldSpan(field) {
  return isLongField(field) ? 24 : 12
}

function displayField(row, field) {
  const value = row?.[field?.name]
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  if (/率/.test(String(field?.label || ''))) return fmtRate(value)
  if (field?.type === 'number' || field?.type === 'money') return fmtNumber(value)
  if (field?.type === 'date') return String(value).slice(0, 10)
  return String(value)
}

const metricDrafts = reactive({})
const metricTextDrafts = reactive({})

function draftKey(row) {
  return `${row?.section || ''}::${row?.key || row?.label || ''}`
}

function findExistingMetric(row) {
  return reportMetrics.value.find((item) => item.section === row.section && item.metric_key === row.key)
    || reportMetrics.value.find((item) => item.metric_key === row.key)
    || reportMetrics.value.find((item) => item.section === row.section && item.label === row.label)
    || reportMetrics.value.find((item) => item.label === row.label)
    || null
}

function syncMetricDrafts() {
  for (const group of Object.values(metricGroups.value).flat()) {
    for (const row of group.rows) {
      const key = draftKey(row)
      const existing = findExistingMetric(row)
      metricDrafts[key] = toNumber(existing?.value_num)
      metricTextDrafts[key] = existing?.value_text || ''
    }
  }
}

async function saveMetrics(group) {
  if (!props.reportId) return
  savingMetrics.value = group.key
  try {
    const items = group.rows.map((row, index) => {
      const existing = findExistingMetric(row)
      const key = draftKey(row)
      return {
        section: row.section,
        metric_key: row.key,
        label: row.label,
        value_num: toNumber(metricDrafts[key]),
        value_text: metricTextDrafts[key] || '',
        unit: row.unit || existing?.unit || '',
        extra: existing?.extra ?? null,
        sort: existing?.sort ?? index,
      }
    })
    await api.put(`/business/manage/reports/${props.reportId}/metrics`, { items })
    await loadDetail()
  } catch {
    /* 拦截器已提示 */
  } finally {
    savingMetrics.value = ''
  }
}

const recordDialog = ref(false)
const recordSection = ref(null)
const recordFields = ref([])
const recordEditingId = ref(null)
const recordForm = reactive({})

function normalizeFormValue(value, field) {
  if (field?.type === 'number') return toNumber(value)
  if (field?.type === 'date') return value ? String(value).slice(0, 10) : ''
  return value === null || value === undefined ? '' : String(value)
}

function openRecord(section, row = null) {
  if (!props.reportId) {
    ElMessage.warning('请先导入或选择批次')
    return
  }
  const fields = fieldsOf(section)
  recordSection.value = section
  recordFields.value = fields
  recordEditingId.value = row?.id ?? null
  for (const key of Object.keys(recordForm)) delete recordForm[key]
  for (const field of fields) {
    const source = field.payload ? row?.payload?.[field.name] : row?.[field.name]
    recordForm[field.name] = normalizeFormValue(source, field)
  }
  recordDialog.value = true
}

async function saveRecord() {
  if (saving.value || !recordSection.value?.key) return
  const payload = { section: recordSection.value.key }
  const extra = {}
  for (const field of recordFields.value) {
    const value = recordForm[field.name]
    const normalized = field.type === 'number'
      ? toNumber(value)
      : field.type === 'date'
        ? (value || null)
        : (typeof value === 'string' ? value.trim() : value)
    // payload 字段（上周进度/责任书目标值/宣发明细等）没有独立列，必须放进 payload 子对象
    if (field.payload) extra[field.name] = normalized
    else payload[field.name] = normalized
  }
  if (Object.keys(extra).length) payload.payload = extra
  const hasContent = recordFields.value.some((field) => {
    const bucket = field.payload ? extra : payload
    const value = bucket[field.name]
    return value !== null && value !== undefined && value !== ''
  })
  if (!hasContent) {
    ElMessage.warning('请至少填写一项内容')
    return
  }
  saving.value = true
  try {
    if (recordEditingId.value) {
      await api.put(`/business/manage/records/${recordEditingId.value}`, payload)
    } else {
      await api.post(`/business/manage/reports/${props.reportId}/records`, payload)
    }
    recordDialog.value = false
    await loadDetail()
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

async function removeRecord(row) {
  const label = row?.name || row?.content || `#${row?.id}`
  try {
    await ElMessageBox.confirm(`确认删除记录「${label}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.delete(`/business/manage/records/${row.id}`)
    await loadDetail()
  } catch {
    /* 拦截器已提示 */
  }
}

const kpiChart = ref(null)
const incomeChart = ref(null)
const leasingChart = ref(null)
const litigationChart = ref(null)
let chartInstances = {}
let detachResize = () => {}

function renderCharts() {
  const options = {
    kpi: kpiOption(overview.value.series.kpi_completion || []),
    income: collectionTrendOption(overview.value.series.collection_trend || []),
    leasing: leasingOption(overview.value.series.leasing_progress || []),
    litigation: litigationOption(
      overview.value.series.litigation_by_stage || [],
      overview.value.series.litigation_top || [],
    ),
  }
  const refs = {
    kpi: kpiChart.value,
    income: incomeChart.value,
    leasing: leasingChart.value,
    litigation: litigationChart.value,
  }
  for (const [key, element] of Object.entries(refs)) {
    if (!element) continue
    chartInstances[key] ??= echarts.init(element)
    chartInstances[key].setOption(options[key], true)
  }
}

async function loadOverview() {
  try {
    const { data } = await api.get('/business/manage/overview', { params: { months: 12 } })
    overview.value = normalizeOverview(data)
  } catch {
    overview.value = emptyOverview()
  }
}

async function loadDetail() {
  if (!props.reportId) {
    detail.value = null
    return
  }
  try {
    const { data } = await api.get(`/business/manage/reports/${props.reportId}`)
    detail.value = data || null
    syncMetricDrafts()
  } catch {
    detail.value = null
  }
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
  () => [props.reportId, props.refreshKey],
  () => load(),
)

// meta 与报表详情并发加载：meta 后到时按新规格重建草稿，避免指标输入框显示为空后误保存
watch(
  () => props.meta,
  () => syncMetricDrafts(),
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

.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 12px 0 8px;
}

.block-title {
  font-weight: 600;
}

.record-block + .record-block,
.metric-group + .metric-group {
  margin-top: 16px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
</style>
