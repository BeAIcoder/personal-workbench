<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>银行回单整理</span>
        </div>
      </template>
      <el-alert type="info" :closable="false" show-icon
        title="按账期扫描建行、平安、中行回单，逐笔复核分类后，按银行账户和业务类别生成 A4 分册（建行/平安每页 3 笔，中行每页 2 笔）。账期起止可手动修改，不受固定日期限制。" />
      <el-form size="small" inline class="period-form" @submit.prevent>
        <el-form-item label="账期月份">
          <el-date-picker v-model="periodMonth" type="month" placeholder="选择月份" value-format="YYYY-MM"
            style="width: 130px" @change="applySuggestedPeriod" />
        </el-form-item>
        <el-form-item label="账期名称">
          <el-input v-model="periodLabel" style="width: 120px" clearable />
        </el-form-item>
        <el-form-item label="开始日期">
          <el-date-picker v-model="startDate" type="date" placeholder="起始日" value-format="YYYY-MM-DD" style="width: 150px" />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker v-model="endDate" type="date" placeholder="结束日" value-format="YYYY-MM-DD" style="width: 150px" />
        </el-form-item>
      </el-form>
      <el-form size="small" inline @submit.prevent>
        <el-form-item label="回单源目录">
          <div class="dir-cell">
            <el-input v-model="sourceDir" placeholder="粘贴回单所在目录" clearable style="width: 340px" />
            <el-button size="small" @click="pickSource = true">浏览</el-button>
          </div>
        </el-form-item>
        <el-form-item label="输出目录">
          <div class="dir-cell">
            <el-input v-model="outputDir" :placeholder="defaultOutputDir ? `留空落 ${defaultOutputDir}` : '留空落工作台数据目录'"
              clearable style="width: 300px" />
            <el-button size="small" @click="pickOutput = true">浏览</el-button>
          </div>
        </el-form-item>
        <el-form-item>
          <el-button size="small" type="primary" :loading="scanning" :disabled="!sourceDir || !startDate || !endDate"
            @click="scan">扫描并建立复核批次</el-button>
        </el-form-item>
      </el-form>
      <DirectoryPicker v-model="pickSource" title="选择回单源目录" @select="p => (sourceDir = p)" />
      <DirectoryPicker v-model="pickOutput" title="选择输出目录" @select="p => (outputDir = p)" />
    </el-card>

    <el-card v-if="batch" shadow="never" style="margin-top: 12px">
      <template #header>
        <div class="card-head">
          <span>复核批次 #{{ batch.id }}（{{ batch.period_label }}：{{ batch.start_date }} ~ {{ batch.end_date }}）</span>
          <div class="toolbar">
            <el-select v-model="categoryFilter" size="small" clearable placeholder="按分类筛选" style="width: 170px">
              <el-option v-for="opt in categoryOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
            <el-button size="small" type="primary" :loading="generating" :disabled="!canGenerate" @click="generate">
              生成 PDF 与清单
            </el-button>
          </div>
        </div>
      </template>
      <el-alert v-if="batch.summary.pending_confirm" type="warning" :closable="false" show-icon
        :title="`仍有 ${batch.summary.pending_confirm} 笔待确认（未分类），请先在下表确定分类或排除后再生成`" />
      <el-alert v-for="f in batch.failed_sources" :key="f.name" type="error" :closable="false" show-icon
        :title="`无法读取：${f.name}（${f.reason}）`" style="margin-top: 6px" />
      <div class="summary-tags">
        <el-tag v-for="opt in categoryOptions" :key="opt.value" size="small" style="margin-right: 6px">
          {{ opt.label }} {{ batch.summary.counts[opt.value] || 0 }} 笔
        </el-tag>
        <el-tag size="small" type="warning" style="margin-right: 6px">待确认 {{ batch.summary.counts.unclassified || 0 }} 笔</el-tag>
        <el-tag size="small" type="info" style="margin-right: 6px">排除 {{ batch.summary.excluded }} 笔</el-tag>
        <el-tag size="small" type="info">重复忽略 {{ batch.summary.duplicates }} 笔</el-tag>
      </div>
      <el-table :data="visibleItems" size="small" style="margin-top: 10px" max-height="460" empty-text="没有符合条件的回单">
        <el-table-column label="纳入" width="70" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.included" size="small" @change="v => patchItem(row, { included: v })" />
          </template>
        </el-table-column>
        <el-table-column prop="rdate" label="日期" width="110" />
        <el-table-column prop="bank" label="银行" width="70" />
        <el-table-column prop="account_no" label="账号" min-width="170" show-overflow-tooltip />
        <el-table-column label="金额(元)" width="120" align="right">
          <template #default="{ row }">{{ row.amount }}</template>
        </el-table-column>
        <el-table-column label="对方/摘要" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.category === 'bank_fee_expense' || row.category === 'bank_interest_income' ? row.purpose : (row.payer || row.payee) }}</template>
        </el-table-column>
        <el-table-column label="分类" width="170">
          <template #default="{ row }">
            <el-select :model-value="row.category" size="small" @change="v => patchItem(row, { category: v })">
              <el-option v-for="opt in categoryOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
              <el-option v-if="row.category === 'unclassified'" value="unclassified" label="待确认" disabled />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="复核" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="row.reviewed ? 'success' : 'info'" size="small">{{ row.reviewed ? '已复核' : '未复核' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="原单" width="70" align="center">
          <template #default="{ row }">
            <el-button size="small" link type="primary" :loading="!!previewing[row.id]" @click="previewItem(row)">预览</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" style="margin-top: 12px">
      <template #header>
        <div class="card-head">
          <span>历史批次与生成产物</span>
          <div class="toolbar">
            <el-button size="small" @click="loadBatches">刷新</el-button>
          </div>
        </div>
      </template>
      <el-table :data="batches" size="small" empty-text="暂无回单整理批次" @expand-change="onBatchExpand">
        <el-table-column type="expand">
          <template #default="{ row }">
            <el-table :data="row._files" size="small" :row-key="f => f.id"
              @selection-change="rows => onFileSelect(row, rows)">
              <el-table-column type="selection" width="42" :selectable="f => f.kind === 'category'" />
              <el-table-column label="类型" width="110">
                <template #default="{ row: f }">
                  <el-tag size="small" :type="f.kind === 'index' ? 'info' : 'success'">{{ f.kind === 'index' ? '分类清单' : f.label }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="文件名" min-width="320" show-overflow-tooltip />
              <el-table-column prop="count" label="笔数" width="70" align="right" />
              <el-table-column prop="pages" label="页数" width="70" align="right" />
              <el-table-column label="操作" width="160" fixed="right">
                <template #default="{ row: f }">
                  <el-button size="small" link type="primary" @click="downloadFile(row, f)">下载</el-button>
                  <el-button v-if="f.kind === 'category'" size="small" link type="primary"
                    :loading="!!filePrinting[row.id + ':' + f.id]" :disabled="!!filePrinting[row.id + ':' + f.id]"
                    @click="printFile(row, f)">打印</el-button>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="period_label" label="账期" width="110" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="回单" width="90" align="right">
          <template #default="{ row }">{{ row.summary.total }} 笔</template>
        </el-table-column>
        <el-table-column label="产物" width="80" align="right">
          <template #default="{ row }">{{ row._files.length }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openBatch(row)">复核</el-button>
            <el-button size="small" link type="primary" :disabled="!row._files.length" @click="zipDownload(row)">整批 ZIP</el-button>
            <el-button size="small" link type="primary" :disabled="!selectedFiles(row).length" @click="mergeDownload(row)">合并下载选中</el-button>
            <el-button size="small" link type="primary" :disabled="!selectedFiles(row).length || !!mergePrinting[row.id]"
              :loading="!!mergePrinting[row.id]" @click="mergePrint(row)">合并打印选中</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import api from '../../../api'
import DirectoryPicker from '../../../components/DirectoryPicker.vue'
import { saveBlob, openPrintWindow, showPdfInWindow } from '../../../utils/download.js'

const BASE = '/finance/receipt'
const DIR_KEYS = { source: 'receipt_source_dir', output: 'receipt_output_dir' }
const CATEGORY_OPTIONS = [
  { value: 'pending_clearing_income', label: '待清算回单收入' },
  { value: 'transfer_income', label: '正常转账收入' },
  { value: 'transfer_expense', label: '转账支出' },
  { value: 'bank_interest_income', label: '银行结息收入' },
  { value: 'bank_fee_expense', label: '银行手续费支出' },
]

const loading = ref(false)
const scanning = ref(false)
const generating = ref(false)
const periodMonth = ref('')
const periodLabel = ref('')
const startDate = ref('')
const endDate = ref('')
const sourceDir = ref('')
const outputDir = ref('')
const defaultOutputDir = ref('')
const pickSource = ref(false)
const pickOutput = ref(false)
const batch = ref(null)
const batches = ref([])
const categoryFilter = ref('')
const fileSelection = reactive({})
const filePrinting = reactive({})
const mergePrinting = reactive({})
const previewing = reactive({})

const categoryOptions = CATEGORY_OPTIONS

const visibleItems = computed(() => {
  if (!batch.value) return []
  const items = batch.value.items || []
  return categoryFilter.value ? items.filter(item => item.category === categoryFilter.value) : items
})

const canGenerate = computed(() => {
  if (!batch.value) return false
  return batch.value.summary.pending_confirm === 0 && batch.value.summary.total - batch.value.summary.excluded > 0
})

function lsGet(key) {
  try { return globalThis.localStorage?.getItem(key) || '' } catch { return '' }
}

function lsSet(key, value) {
  try { globalThis.localStorage?.setItem(key, value) } catch { /* 无 localStorage 环境忽略 */ }
}

function applySuggestedPeriod(month) {
  if (!month) return
  const base = dayjs(`${month}-01`)
  periodLabel.value = month
  startDate.value = base.subtract(1, 'month').date(25).format('YYYY-MM-DD')
  endDate.value = base.endOf('month').format('YYYY-MM-DD')
}

function statusType(status) {
  return { scanned: 'info', ok: 'success', failed: 'danger' }[status] || 'info'
}

function statusText(status) {
  return { scanned: '待复核', ok: '已生成', failed: '生成失败' }[status] || status
}

function withFiles(row) {
  return { ...row, _files: row.files || [] }
}

async function loadConfig() {
  try {
    const { data } = await api.get(`${BASE}/config`)
    defaultOutputDir.value = data.configured_output_dir || data.default_output_dir || ''
    sourceDir.value = lsGet(DIR_KEYS.source) || data.configured_source_dir || ''
    outputDir.value = lsGet(DIR_KEYS.output) || ''
  } catch {
    /* 拦截器已统一提示；目录默认值拿不到就留空 */
  }
}

async function loadBatches() {
  try {
    const { data } = await api.get(`${BASE}/batches`, { params: { limit: 50 } })
    batches.value = (data.items || []).map(withFiles)
    for (const key of Object.keys(fileSelection)) delete fileSelection[key]
  } catch {
    batches.value = []
  }
}

async function loadDetail(id) {
  try {
    const { data } = await api.get(`${BASE}/batches/${id}`)
    batch.value = data
    return data
  } catch {
    /* 拦截器已统一提示 */
    return null
  }
}

async function scan() {
  if (!sourceDir.value || !startDate.value || !endDate.value) return
  scanning.value = true
  lsSet(DIR_KEYS.source, sourceDir.value)
  lsSet(DIR_KEYS.output, outputDir.value || '')
  try {
    const { data } = await api.post(`${BASE}/scan`, {
      source_dir: sourceDir.value,
      period_label: periodLabel.value || startDate.value.slice(0, 7),
      start_date: startDate.value,
      end_date: endDate.value,
      output_dir: outputDir.value || '',
    }, { timeout: 300000 })
    batch.value = data
    ElMessage.success(`扫描完成：共 ${data.summary.total} 笔，待确认 ${data.summary.counts.unclassified || 0} 笔`)
    await loadBatches()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    scanning.value = false
  }
}

async function patchItem(row, patch) {
  if (!batch.value) return
  try {
    await api.patch(`${BASE}/batches/${batch.value.id}/items/${row.id}`, patch)
    await loadDetail(batch.value.id)
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function generate() {
  if (!batch.value || !canGenerate.value) return
  generating.value = true
  try {
    const { data } = await api.post(`${BASE}/batches/${batch.value.id}/generate`, null, { timeout: 600000 })
    batch.value = data
    ElMessage.success(`生成完成：${data.files.filter(f => f.kind === 'category').length} 个分册与 1 份清单`)
    await loadBatches()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    generating.value = false
  }
}

function openBatch(row) {
  loadDetail(row.id)
}

function onBatchExpand(row, expanded) {
  if (expanded) row._files = row.files || row._files || []
}

function onFileSelect(row, rows) {
  fileSelection[row.id] = rows.map(f => f.id)
}

function selectedFiles(row) {
  return fileSelection[row.id] || []
}

async function downloadFile(row, f) {
  try {
    const resp = await api.get(`${BASE}/batches/${row.id}/files/${encodeURIComponent(f.id)}`, { responseType: 'blob' })
    saveBlob(resp.data, f.name)
  } catch {
    /* 拦截器已统一提示 */
  }
}

function closeWindow(win) {
  try {
    win.close()
  } catch {
    /* 页签已关闭或浏览器禁止脚本关窗 */
  }
}

async function printFile(row, f) {
  const key = `${row.id}:${f.id}`
  if (filePrinting[key]) return
  const win = openPrintWindow()
  if (!win) {
    ElMessage.warning('新标签页被浏览器拦截：请允许本站弹窗，或改用「下载」后在 PDF 阅读器中打印')
    return
  }
  filePrinting[key] = true
  try {
    const { data } = await api.get(`${BASE}/batches/${row.id}/files/${encodeURIComponent(f.id)}`,
      { responseType: 'blob', timeout: 300000 })
    if (!showPdfInWindow(data, { window: win })) throw new Error('无法在新标签页打开 PDF')
    ElMessage.info('正在新标签页加载 PDF；若未弹出打印对话框，请按 Ctrl+P，或改用「下载」')
  } catch {
    closeWindow(win)
  } finally {
    delete filePrinting[key]
  }
}

async function previewItem(item) {
  if (!batch.value || previewing[item.id]) return
  const win = openPrintWindow()
  if (!win) {
    ElMessage.warning('新标签页被浏览器拦截：请允许本站弹窗后重试')
    return
  }
  previewing[item.id] = true
  try {
    const { data } = await api.get(`${BASE}/batches/${batch.value.id}/items/${item.id}/preview`,
      { responseType: 'blob', timeout: 120000 })
    const url = URL.createObjectURL(data)
    win.location.href = url
    setTimeout(() => URL.revokeObjectURL(url), 600000)
  } catch {
    closeWindow(win)
  } finally {
    delete previewing[item.id]
  }
}

async function mergeDownload(row) {
  const ids = selectedFiles(row)
  if (!ids.length) return
  try {
    const { data } = await api.post(`${BASE}/batches/${row.id}/merge`, { file_ids: ids }, { responseType: 'blob' })
    saveBlob(data, `银行回单合并_${row.period_label}_${ids.length}份.pdf`)
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function mergePrint(row) {
  const ids = selectedFiles(row)
  if (!ids.length || mergePrinting[row.id]) return
  const win = openPrintWindow()
  if (!win) {
    ElMessage.warning('新标签页被浏览器拦截：请允许本站弹窗，或改用「合并下载选中」后在 PDF 阅读器中打印')
    return
  }
  mergePrinting[row.id] = true
  try {
    const { data } = await api.post(`${BASE}/batches/${row.id}/merge`, { file_ids: ids },
      { responseType: 'blob', timeout: 300000 })
    if (!showPdfInWindow(data, { window: win })) throw new Error('无法在新标签页打开 PDF')
    ElMessage.info('正在新标签页加载合并 PDF；若未弹出打印对话框，请按 Ctrl+P')
  } catch {
    closeWindow(win)
  } finally {
    delete mergePrinting[row.id]
  }
}

async function zipDownload(row) {
  try {
    const { data } = await api.get(`${BASE}/batches/${row.id}/download`, { responseType: 'blob', timeout: 300000 })
    saveBlob(data, `银行回单整理_${row.period_label}.zip`)
  } catch {
    /* 拦截器已统一提示 */
  }
}

onMounted(async () => {
  loading.value = true
  try {
    await loadConfig()
    await loadBatches()
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.period-form {
  margin-top: 8px;
}

.dir-cell {
  display: flex;
  gap: 8px;
  align-items: center;
}

.summary-tags {
  margin-top: 10px;
}
</style>
