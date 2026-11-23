<template>
  <div class="manage-panel" v-loading="loading">
    <el-row :gutter="12" class="chart-row">
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>项目收缴率 / 出租率对比</span></template>
          <div ref="compareChart" class="chart" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never" class="chart-card">
          <template #header><span>招商新签 / 续签</span></template>
          <div ref="signingChart" class="chart" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="text-card">
      <template #header>
        <div class="block-head">
          <span class="block-title">月度工作情况</span>
          <span class="hint">
            {{ reportId ? `${currentProject || '当前批次'} · 按小节保存` : '请先导入或选择批次' }}
          </span>
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
            :rows="6"
            :disabled="!reportId"
            placeholder="暂无内容"
            @update:model-value="textDrafts[section.key] = $event"
          />
        </div>
      </div>
      <el-empty
        v-else
        :description="reportId ? '当前批次未识别到月度小节' : '请先导入或选择批次'"
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
  normalizeOverview,
  toNumber,
} from '../../shared.js'
import { projectCompareOption, signingOption } from '../../chartOptions.js'

const props = defineProps({
  meta: { type: Object, default: null },
  reportId: { type: [Number, String], default: null },
  project: { type: String, default: '' },
  refreshKey: { type: Number, default: 0 },
})

const DEFAULT_MONTHLY_TEXTS = [
  { key: 'clues', label: '一、问题线索处置及案件办理' },
  { key: 'key_work', label: '二、重点工作推进' },
  { key: 'team', label: '三、自身建设和队伍建设' },
  { key: 'highlights', label: '四、亮点工作' },
]

const overview = ref(emptyOverview())
const detail = ref(null)
const loading = ref(false)
const savingText = ref('')
const textDrafts = reactive({})
const textMeta = reactive({})

const reportTexts = computed(() => (Array.isArray(detail.value?.texts) ? detail.value.texts : []))
const reportTextKeys = computed(() => new Set(reportTexts.value.map((row) => row.section).filter(Boolean)))
const currentProject = computed(() => detail.value?.project || props.project || '')

const metaTexts = computed(() => (
  Array.isArray(props.meta?.sections?.monthly?.texts) ? props.meta.sections.monthly.texts : []
))

const textSections = computed(() => {
  if (metaTexts.value.length) return metaTexts.value
  if (reportTextKeys.value.size) {
    return [...reportTextKeys.value].map((key) => ({ key, label: key }))
  }
  return DEFAULT_MONTHLY_TEXTS
})

const compareRows = computed(() => overview.value.series.project_compare || [])

const signingRows = computed(() => {
  const metrics = Array.isArray(detail.value?.metrics) ? detail.value.metrics : []
  const newSign = findMetric(metrics, ['新签品牌面积', '新签面积', '新签品牌数量', '新签'])
  const renewal = findMetric(metrics, ['续签品牌面积', '续签面积', '续签品牌数量', '续签'])
  const rows = []
  const newValue = toNumber(newSign?.value_num)
  const renewalValue = toNumber(renewal?.value_num)
  if (newValue !== null) {
    rows.push({ name: newSign.unit ? `新签（${newSign.unit}）` : '新签', value: newValue, unit: newSign.unit || '' })
  }
  if (renewalValue !== null) {
    rows.push({ name: renewal.unit ? `续签（${renewal.unit}）` : '续签', value: renewalValue, unit: renewal.unit || '' })
  }
  return rows
})

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

const compareChart = ref(null)
const signingChart = ref(null)
let chartInstances = {}
let detachResize = () => {}

function renderCharts() {
  const options = {
    compare: projectCompareOption(compareRows.value),
    signing: signingOption(signingRows.value),
  }
  const refs = { compare: compareChart.value, signing: signingChart.value }
  for (const [key, element] of Object.entries(refs)) {
    if (!element) continue
    chartInstances[key] ??= echarts.init(element)
    chartInstances[key].setOption(options[key], true)
  }
}

async function loadOverview() {
  try {
    const { data } = await api.get('/business/manage/overview', {
      params: { project: props.project || undefined, months: 12 },
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

.chart-row {
  margin-bottom: 12px;
}

.chart-card {
  margin-bottom: 12px;
}

.chart {
  height: 280px;
}

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

.text-block .block-head {
  margin: 10px 0 8px;
}

.text-block + .text-block {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
</style>
