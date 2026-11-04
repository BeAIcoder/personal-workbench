<template>
  <div class="page">
    <el-card class="box-card">
      <template #header>
        <span class="card-title">① 上传银行流水（建行/中行银企直联导出的 xlsx）</span>
      </template>
      <el-upload drag action="#" accept=".xlsx" :auto-upload="false" :limit="1"
        :on-change="onFileChange" :on-remove="resetPreview">
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">把银行流水 xlsx 拖到这里，或<em>点击选择</em>（建行/中行）</div>
        <template #tip>
          <div class="el-upload__tip">支持多账户混合流水；解析后按账户汇总预览</div>
        </template>
      </el-upload>
      <div class="row">
        <el-button type="primary" :disabled="!file" :loading="parsing" @click="parseFile">解析预览</el-button>
        <span v-if="summary" class="hint">共 {{ summary.total }} 条流水、{{ summary.accounts.length }} 个账户</span>
      </div>
    </el-card>

    <el-card v-if="summary" class="box-card">
      <template #header>
        <span class="card-title">② 选择账户与日期区间</span>
      </template>
      <el-alert v-if="unmappedCount" type="warning" :closable="false" class="row"
        :title="`${unmappedCount} 个账户未配置 H 编码映射，导出前需在本模块 config.local.yaml 登记该账号`" />
      <el-table :data="accountRows" highlight-current-row @current-change="onSelectAccount">
        <el-table-column label="选择" width="64">
          <template #default="{ row }">
            <el-radio :model-value="selectedAccount" :value="row.account" @change="onSelectAccount(row)">&nbsp;</el-radio>
          </template>
        </el-table-column>
        <el-table-column prop="tail" label="账户尾号" width="100" />
        <el-table-column prop="name" label="项目" width="140">
          <template #default="{ row }">
            <span :class="{ warn: !row.name }">{{ row.name || '未映射' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="ent" label="H 编码" width="120" />
        <el-table-column prop="rows" label="流水条数" width="100" />
        <el-table-column label="日期范围">
          <template #default="{ row }">
            {{ row.date_min }} ~ {{ row.date_max }}
          </template>
        </el-table-column>
      </el-table>
      <div class="row">
        <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
          start-placeholder="开始日期" end-placeholder="结束日期" :clearable="false" />
        <el-button type="primary" :disabled="!selectedAccount || !selectedMapped" :loading="exporting"
          @click="downloadTemplate">③ 生成模板并下载</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, ref, shallowRef } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import api from '../../../api'
import { saveBlob } from '../../../utils/download.js'
import { compactDateToRange, isPreviewCurrent } from '../previewState.js'

const file = shallowRef(null)
const parsing = ref(false)
const exporting = ref(false)
const summary = ref(null)
const previewFile = shallowRef(null)
const selectedAccount = ref('')
const dateRange = ref([])
// 预览请求序号：换文件/重新解析都会推进，旧响应回来时直接丢弃
let previewSeq = 0

const accountRows = computed(() => summary.value?.accounts || [])

const unmappedCount = computed(
  () => (summary.value?.accounts || []).filter((a) => !a.ent).length
)

const selectedMapped = computed(() => {
  const acc = (summary.value?.accounts || []).find((a) => a.account === selectedAccount.value)
  return Boolean(acc?.ent)
})

function onFileChange(uploadFile) {
  previewSeq += 1
  file.value = uploadFile.raw || null
  previewFile.value = null
  summary.value = null
  selectedAccount.value = ''
  dateRange.value = []
}

function resetPreview() {
  previewSeq += 1
  file.value = null
  previewFile.value = null
  summary.value = null
  selectedAccount.value = ''
  dateRange.value = []
}

function selectAccount(account) {
  selectedAccount.value = account.account
  dateRange.value = compactDateToRange(account.date_min, account.date_max)
}

async function parseFile() {
  if (!file.value) return
  const seq = previewSeq + 1
  previewSeq = seq
  const snapshot = file.value
  parsing.value = true
  try {
    const fd = new FormData()
    fd.append('file', snapshot)
    // 最多 20MB xlsx 在请求内同步解析，万行级文件会超全局 10s
    const { data } = await api.post('/finance/bankflow/preview', fd, { timeout: 120000 })
    if (!isPreviewCurrent(seq, previewSeq, snapshot, file.value)) return
    summary.value = data
    previewFile.value = snapshot
    const first = (data.accounts || []).find((a) => a.ent) || (data.accounts || [])[0]
    if (first) selectAccount(first)
  } catch {
    // 错误提示由 axios 拦截器统一弹出（B6 约定），这里只复位状态
  } finally {
    if (seq === previewSeq) parsing.value = false
  }
}

function onSelectAccount(row) {
  if (!row) return
  selectAccount(row)
}

async function downloadTemplate() {
  const acc = (summary.value?.accounts || []).find((a) => a.account === selectedAccount.value)
  if (!acc) return
  if (!previewFile.value || previewFile.value !== file.value) {
    ElMessage.warning('文件已变更，请重新解析预览后再生成模板')
    return
  }
  exporting.value = true
  try {
    const fd = new FormData()
    // 使用预览时的文件快照，避免解析后换文件导致「新文件 + 旧选择」
    fd.append('file', previewFile.value)
    fd.append('account', selectedAccount.value)
    fd.append('date_start', dateRange.value?.[0] || '')
    fd.append('date_end', dateRange.value?.[1] || '')
    // 模板生成要重读流水并复制官方骨架样式，放宽到 3 分钟
    const resp = await api.post('/finance/bankflow/template', fd, { responseType: 'blob', timeout: 180000 })
    const tail = selectedAccount.value.slice(-4)
    const label = acc.bank_label || '银行'
    const start = (dateRange.value?.[0] || '').replace(/-/g, '')
    const end = (dateRange.value?.[1] || '').replace(/-/g, '')
    const name = `${label}${tail}_银行流水导入模板_${start || '全部'}-${end || '全部'}.xlsx`
    saveBlob(resp.data, name)
    ElMessage.success(`已生成并下载（${name}）`)
  } catch {
    // 下载失败提示由 axios 拦截器统一弹出（B6 约定）；Blob 错误体已由拦截器解析 detail
  } finally {
    exporting.value = false
  }
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.card-title { font-weight: 600; }
.row { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.hint { color: var(--el-text-color-secondary); font-size: 13px; }
.warn { color: var(--el-color-warning); }
</style>

