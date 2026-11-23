<template>
  <div class="page manage-shell">
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <div class="toolbar-left">
          <el-select
            v-model="batchId"
            clearable
            filterable
            placeholder="当前批次"
            style="width: 320px"
            :loading="reportsLoading"
            @change="onBatchChange"
          >
            <el-option v-for="item in batchOptions" :key="item.id" :label="batchLabel(item)" :value="item.id" />
          </el-select>
          <el-select v-model="period" clearable placeholder="期间" style="width: 130px" @change="onPeriodChange">
            <el-option v-for="item in periods" :key="item" :label="item" :value="item" />
          </el-select>
        </div>
        <div class="toolbar-right">
          <el-button size="small" @click="refresh">刷新</el-button>
          <el-button size="small" type="primary" @click="importVisible = true">导入报表</el-button>
          <el-button size="small" :disabled="!batchId || exporting" :loading="exporting" @click="exportCurrent">
            导出当前报表
          </el-button>
          <AskAiButton :prompt="aiPrompt" />
        </div>
      </div>
      <div class="toolbar-hint">
        <span>{{ activeDef.label }} · {{ activeDef.project || '全部项目' }}</span>
        <span v-if="currentReport">
          当前批次：{{ currentReport.period || '—' }} · {{ currentReport.project || '—' }} ·
          {{ currentReport.source_file || currentReport.title || '—' }}
        </span>
        <span v-else>暂无批次，请先导入 Excel 报表或切换期间</span>
      </div>
    </el-card>

    <el-tabs :model-value="activeTab" class="manage-tabs" @tab-change="onTabChange">
      <el-tab-pane label="内控管理" name="control">
        <ControlPanel
          v-if="activeTab === 'control'"
          :meta="meta"
          :report-id="batchId"
          :refresh-key="reloadKey"
        />
      </el-tab-pane>
      <el-tab-pane label="云南项目周报" name="weekly">
        <WeeklyPanel
          v-if="activeTab === 'weekly'"
          project="云南项目"
          :meta="meta"
          :report-id="batchId"
          :refresh-key="reloadKey"
        />
      </el-tab-pane>
      <el-tab-pane label="曲靖项目周报" name="weekly-qujing">
        <WeeklyPanel
          v-if="activeTab === 'weekly-qujing'"
          project="曲靖项目"
          :meta="meta"
          :report-id="batchId"
          :refresh-key="reloadKey"
        />
      </el-tab-pane>
      <el-tab-pane label="月度工作情况" name="monthly">
        <MonthlyPanel
          v-if="activeTab === 'monthly'"
          :meta="meta"
          :report-id="batchId"
          :project="currentReport?.project || ''"
          :refresh-key="reloadKey"
        />
      </el-tab-pane>
    </el-tabs>

    <ImportDialog
      v-model="importVisible"
      :meta="meta"
      :report-type="activeDef.reportType"
      :project="currentReport?.project || activeDef.project"
      @imported="onImported"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../api'
import { saveBlob } from '../../../utils/download.js'
import { sanitizeFilename } from '../shared.js'
import AskAiButton from '../../../components/assistant/AskAiButton.vue'
import ControlPanel from './parts/ControlPanel.vue'
import WeeklyPanel from './parts/WeeklyPanel.vue'
import MonthlyPanel from './parts/MonthlyPanel.vue'
import ImportDialog from './parts/ImportDialog.vue'

const TAB_DEFS = {
  control: { label: '内控管理', path: '/business/manage/control', reportType: 'control', project: '' },
  weekly: { label: '云南项目周报', path: '/business/manage/weekly', reportType: 'weekly', project: '云南项目' },
  'weekly-qujing': { label: '曲靖项目周报', path: '/business/manage/weekly-qujing', reportType: 'weekly', project: '曲靖项目' },
  monthly: { label: '月度工作情况', path: '/business/manage/monthly', reportType: 'monthly', project: '' },
}
const TAB_BY_PATH = Object.fromEntries(Object.entries(TAB_DEFS).map(([name, def]) => [def.path, name]))

const route = useRoute()
const router = useRouter()

const meta = ref(null)
const reports = ref([])
const reportsLoading = ref(false)
const batchId = ref(null)
const period = ref('')
const importVisible = ref(false)
const exporting = ref(false)
const reloadKey = ref(0)

const activeTab = computed(() => TAB_BY_PATH[route.path] || 'control')
const activeDef = computed(() => TAB_DEFS[activeTab.value])
const currentReport = computed(() => reports.value.find((item) => item.id === batchId.value) || null)
const periods = computed(() => {
  const values = reports.value.map((item) => item?.period).filter(Boolean)
  return [...new Set(values)].sort((a, b) => String(b).localeCompare(String(a)))
})
const batchOptions = computed(() => {
  if (!period.value) return reports.value
  const filtered = reports.value.filter((item) => item?.period === period.value)
  return filtered.length ? filtered : reports.value
})
const aiPrompt = computed(() => {
  const report = currentReport.value
  const scope = report
    ? `${report.project || activeDef.value.project || '全部项目'} ${report.period || ''}`.trim()
    : activeDef.value.label
  return `请分析经营管理「${activeDef.value.label}」（${scope}）：关键指标完成情况、缺口原因、风险与下一步建议`
})

function batchLabel(item) {
  if (!item) return ''
  return [item.period, item.project, item.source_file || item.title || `批次 #${item.id}`]
    .filter(Boolean)
    .join(' · ')
}

async function loadMeta() {
  try {
    const { data } = await api.get('/business/manage/meta')
    meta.value = data || null
  } catch {
    meta.value = null
  }
}

let reportsRequestSeq = 0

async function loadReports(preferredId = null) {
  const seq = ++reportsRequestSeq
  const def = activeDef.value
  reportsLoading.value = true
  try {
    const params = { report_type: def.reportType, page_size: 50 }
    const { data } = await api.get('/business/manage/reports', { params })
    if (seq !== reportsRequestSeq) return
    let items = Array.isArray(data?.items) ? data.items : []
    if (def.project) {
      const keyword = def.project.replace('项目', '')
      const matched = items.filter((item) => String(item.project || '').includes(keyword))
      if (matched.length) items = matched
    }
    reports.value = items
    const target = reports.value.find((item) => item.id === preferredId) || reports.value[0] || null
    batchId.value = target?.id ?? null
    period.value = target?.period ?? ''
  } catch {
    if (seq !== reportsRequestSeq) return
    reports.value = []
    batchId.value = null
    period.value = ''
  } finally {
    if (seq === reportsRequestSeq) reportsLoading.value = false
  }
}

function onBatchChange(value) {
  const report = reports.value.find((item) => item.id === value)
  if (report?.period) period.value = report.period
}

function onPeriodChange() {
  const current = reports.value.find((item) => item.id === batchId.value)
  if (current?.period === period.value) return
  const hit = reports.value.find((item) => item?.period === period.value)
  batchId.value = hit?.id ?? null
}

function onTabChange(name) {
  const def = TAB_DEFS[name]
  if (!def || route.path === def.path) return
  router.push(def.path)
}

async function refresh() {
  reloadKey.value += 1
  await loadReports(batchId.value)
}

async function onImported(payload) {
  reloadKey.value += 1
  await loadReports(payload?.report_id ?? null)
}

async function exportCurrent() {
  if (!batchId.value) {
    ElMessage.warning('请先选择一个批次')
    return
  }
  const report = currentReport.value
  const project = report?.project || activeDef.value.project || '全部项目'
  const month = report?.period || period.value || ''
  exporting.value = true
  try {
    const { data } = await api.get(`/business/manage/reports/${batchId.value}/export.xlsx`, {
      responseType: 'blob',
      timeout: 120000,
    })
    saveBlob(data, `经营管理-${sanitizeFilename(project)}-${sanitizeFilename(month)}.xlsx`)
  } catch {
    /* 拦截器已提示 */
  } finally {
    exporting.value = false
  }
}

watch(
  () => route.path,
  (path) => {
    if (!TAB_BY_PATH[path]) return
    batchId.value = null
    period.value = ''
    loadReports(null)
  },
)

onMounted(() => {
  loadMeta()
  loadReports(null)
})
</script>

<style scoped>
.toolbar-card {
  margin-bottom: 12px;
}

.toolbar,
.toolbar-left,
.toolbar-right,
.toolbar-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.toolbar {
  justify-content: space-between;
}

.toolbar-right {
  justify-content: flex-end;
}

.toolbar-hint {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.toolbar-hint span + span::before {
  content: '｜';
  margin-right: 8px;
  color: var(--el-border-color);
}
</style>
