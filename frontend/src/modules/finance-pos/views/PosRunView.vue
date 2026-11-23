<template>
  <div class="page" v-loading="loading">
    <HelpLink section="6.4" />
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>建行 POS 收款对账</span>
          <div class="toolbar">
            <el-button size="small" type="primary" @click="dialog.visible = true">新建运行</el-button>
            <AskAiButton :prompt="aiPrompt" />
          </div>
        </div>
      </template>
      <el-alert type="info" :closable="false" show-icon
        title="上传 SHOP 日对账单、认领记录、辅助明细账、凭证列表四份文件（或打一个 ZIP），由本机引擎生成 POS 项目台账并做四源交叉差异分析，产物可打包下载。" />
      <el-table :data="items" size="small" style="margin-top: 12px" empty-text="暂无运行记录">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入文件" min-width="200">
          <template #default="{ row }">
            <el-tag v-for="f in row.input_files" :key="f.name" size="small" style="margin-right: 4px">{{ f.name }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="产物" width="80" align="right">
          <template #default="{ row }">{{ row.artifacts.length }}</template>
        </el-table-column>
        <el-table-column prop="elapsed_sec" label="耗时(秒)" width="90" align="right" />
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'uploaded'" size="small" link type="primary" :loading="runningId === row.id" @click="execute(row)">执行</el-button>
            <el-button size="small" link type="primary" :disabled="!row.artifacts.length" @click="downloadZip(row)">打包下载</el-button>
            <el-button v-if="row.error || row.log_tail" size="small" link type="warning" @click="showLog(row)">日志</el-button>
            <el-button size="small" link type="danger" @click="deleteRun(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" style="margin-top: 12px">
      <template #header>
        <div class="card-head">
          <span>刷卡凭证（回单 × 对账单合并）</span>
          <div class="toolbar">
            <el-date-picker v-model="voucherMonth" type="month" placeholder="选择月份" value-format="YYYY-MM" style="width: 130px" />
            <el-button size="small" type="primary" :loading="generating" :disabled="!voucherMonth" @click="generateVouchers">生成凭证</el-button>
          </div>
        </div>
      </template>
      <el-alert type="info" :closable="false" show-icon
        title="按月扫描源目录里的 SHOP 对账单与银行回单 PDF：自动匹配后，每笔交易生成「对账单页+回单页」合并凭证，并汇总生成月度刷卡手续费凭证；对账单自动缩放为 A4 横排。源目录填归档基目录或当月目录均可；输出目录留空落工作台数据目录。" />
      <el-form size="small" inline style="margin-top: 8px" @submit.prevent>
        <el-form-item label="源目录">
          <div class="dir-cell">
            <el-input v-model="voucherStatementDir" placeholder="粘贴归档基目录或当月目录（留空用 .env 默认）" clearable style="width: 340px" />
            <el-button size="small" @click="pickSource = true">浏览</el-button>
          </div>
        </el-form-item>
        <el-form-item label="输出目录">
          <div class="dir-cell">
            <el-input v-model="voucherOutputDir" :placeholder="defaultOutputDir ? `留空落 ${defaultOutputDir}` : '留空落工作台数据目录'" clearable style="width: 280px" />
            <el-button size="small" @click="pickOutput = true">浏览</el-button>
          </div>
        </el-form-item>
      </el-form>
      <DirectoryPicker v-model="pickSource" title="选择源目录（对账单+回单所在月度目录或其基目录）" @select="p => (voucherStatementDir = p)" />
      <DirectoryPicker v-model="pickOutput" title="选择输出目录（凭证保存位置）" @select="p => (voucherOutputDir = p)" />
      <el-table :data="voucherBatches" size="small" style="margin-top: 12px" empty-text="暂无凭证批次" @expand-change="onBatchExpand">
        <el-table-column type="expand">
          <template #default="{ row }">
            <el-table :data="row._files" size="small" @selection-change="rows => onFileSelect(row, rows)">
              <el-table-column type="selection" width="42" />
              <el-table-column label="类型" width="90">
                <template #default="{ row: f }">
                  <el-tag size="small" :type="f.kind === 'fee' ? 'warning' : 'success'">{{ f.kind === 'fee' ? '手续费凭证' : '交易凭证' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="文件名" min-width="320" show-overflow-tooltip />
              <el-table-column label="大小" width="90" align="right">
                <template #default="{ row: f }">{{ Math.round(f.size / 1024) }}KB</template>
              </el-table-column>
              <el-table-column label="操作" width="130" fixed="right">
                <template #default="{ row: f }">
                  <el-button size="small" link type="primary" @click="downloadFile(row, f)">下载</el-button>
                  <el-button
                    size="small"
                    link
                    :loading="!!filePrinting[row.id + ':' + f.name]"
                    :disabled="!!filePrinting[row.id + ':' + f.name]"
                    @click="printVoucherFile(row, f)"
                  >打印</el-button>
                </template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="month" label="月份" width="90" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="匹配" width="120" align="right">
          <template #default="{ row }">{{ (row.summary.matched ?? 0) }}/{{ row.summary.receipts_total ?? 0 }} 笔</template>
        </el-table-column>
        <el-table-column label="文件" width="70" align="right">
          <template #default="{ row }">{{ row.files.length }}</template>
        </el-table-column>
        <el-table-column prop="elapsed_sec" label="耗时(秒)" width="90" align="right" />
        <el-table-column prop="created_at" label="生成时间" width="170" />
        <el-table-column label="输出目录" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.output_dir || '（工作台数据目录）' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" :disabled="!selectedFiles(row).length" @click="mergeDownload(row)">合并下载选中</el-button>
            <el-button
              size="small"
              link
              :loading="!!mergePrinting[row.id]"
              :disabled="!selectedFiles(row).length || !!mergePrinting[row.id]"
              @click="mergePrint(row)"
            >合并打印选中</el-button>
            <el-button v-if="row.error || row.log_tail" size="small" link type="warning" @click="showLog(row)">日志</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialog.visible" title="新建 POS 对账运行" width="520px">
      <el-form label-width="90px">
        <el-form-item label="输入文件">
          <input type="file" multiple accept=".xlsx,.xls,.zip" @change="onFiles" />
          <div class="hint">SHOP 日对账单（zip/xlsx）+ 认领记录 + 辅助明细账 + 凭证列表；可打 ZIP。单个文件不超过 50MB、全部文件合计不超过 100MB，超限文件会在上传前自动跳过。</div>
          <div class="tpl-row"><TemplateLinks type="pos" /></div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="creating" :disabled="!form.files.length" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="logDialog" title="运行日志" width="680px">
      <pre class="log">{{ logContent }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import HelpLink from '../../../components/HelpLink.vue'
import api from '../../../api'
import TemplateLinks from '../../../components/TemplateLinks.vue'
import DirectoryPicker from '../../../components/DirectoryPicker.vue'
import { takeFiles } from '../../_shared/fileInput'
import AskAiButton from '../../../components/assistant/AskAiButton.vue'
import { saveBlob, openPrintWindow, showPdfInWindow } from '../../../utils/download.js'

const loading = ref(false)
const items = ref([])
const runningId = ref(null)
const dialog = reactive({ visible: false })
const creating = ref(false)
const form = reactive({ files: [] })
const logDialog = ref(false)
const logContent = ref('')

const aiPrompt = computed(() => '请分析最近一次 POS 对账运行：四源差异、未认领金额与下一步处理建议')

const STATUS_TEXT = { uploaded: '待执行', running: '执行中', ok: '已完成', failed: '失败' }
const STATUS_TYPE = { uploaded: 'info', running: 'warning', ok: 'success', failed: 'danger' }
const statusText = (s) => STATUS_TEXT[s] || s
const statusType = (s) => STATUS_TYPE[s] || 'info'

// 与后端 service.MAX_UPLOAD_BYTES / MAX_BATCH_BYTES 对齐：上传前本地预检，
// 超限文件直接跳过并提示，避免必然被后端 400 的无效上传
const MAX_FILE_BYTES = 50 * 1024 * 1024
const MAX_TOTAL_BYTES = 100 * 1024 * 1024
const fmtMB = (bytes) => `${Math.round(bytes / 1024 / 1024)}MB`

// 执行串行跑「台账 + 差异分析」两个引擎脚本，后端每次 subprocess 超时 900s
// （finance_pos/service.py::STEP_TIMEOUT）→ 最坏 1800s；前端 600s 会先断。
const POS_EXECUTE_TIMEOUT_MS = 1900 * 1000

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/pos/runs')
    items.value = data.items || []
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function onFiles(e) {
  const picked = takeFiles(e)
  const accepted = []
  let total = 0
  for (const f of picked) {
    if (f.size > MAX_FILE_BYTES) {
      ElMessage.warning(`已跳过 ${f.name}：单个文件超过 ${fmtMB(MAX_FILE_BYTES)} 上限`)
      continue
    }
    if (total + f.size > MAX_TOTAL_BYTES) {
      ElMessage.warning(`已跳过 ${f.name}：本次上传合计超过 ${fmtMB(MAX_TOTAL_BYTES)} 上限`)
      continue
    }
    total += f.size
    accepted.push(f)
  }
  form.files = accepted
}

async function create() {
  if (!form.files.length) return
  creating.value = true
  try {
    const fd = new FormData()
    for (const f of form.files) fd.append('files', f)
    const { data } = await api.post('/finance/pos/runs', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    ElMessage.success(`运行 #${data.id} 已创建，点「执行」开始对账`)
    dialog.visible = false
    form.files = []
    await load()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    creating.value = false
  }
}

async function execute(row, project = '') {
  runningId.value = row.id
  try {
    const query = project && project.trim() ? `?project=${encodeURIComponent(project.trim())}` : ''
    const { data } = await api.post(`/finance/pos/runs/${row.id}/execute${query}`, {}, { timeout: POS_EXECUTE_TIMEOUT_MS })
    if (data.status === 'ok') ElMessage.success(`执行完成，产物 ${data.artifacts.length} 个`)
    else ElMessage.error(`执行失败：${data.error || '未知原因'}`)
    await load()
  } catch (err) {
    // 引擎无法从辅助明细账自动识别项目（错误要求 --project）时，后端支持 project 参数
    // 但此前 UI 无处填写——弹窗补填后带参重试一次，其余错误维持拦截器统一提示
    const detail = err?.response?.data?.detail || ''
    if (!project && (detail.includes('--project') || detail.includes('识别项目'))) {
      try {
        const { value } = await ElMessageBox.prompt(
          '引擎无法自动识别或匹配项目，请输入台账中的项目名（可用项目名见错误提示）后重试',
          '指定项目',
          { confirmButtonText: '重试执行', cancelButtonText: '取消' },
        )
        if (value && value.trim()) {
          runningId.value = null
          return execute(row, value)
        }
      } catch {
        /* 用户取消 */
      }
    } else {
      /* 拦截器已统一提示 */
    }
    await load()
  } finally {
    runningId.value = null
  }
}

// 打包下载统一走 axios blob + saveBlob：window.open 裸链在失败时静默，
// 也无法显式落中文名（浏览器会按 URL 猜名）
async function downloadZip(row) {
  try {
    const { data } = await api.get(`/finance/pos/runs/${row.id}/download`, {
      responseType: 'blob',
      timeout: 120000,
    })
    saveBlob(data, `POS对账_运行${row.id}.zip`)
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function deleteRun(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除运行 #${row.id} 吗？运行目录与产物将一并清理，不可恢复。`,
      '删除运行',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await api.delete(`/finance/pos/runs/${row.id}`)
    ElMessage.success(`运行 #${row.id} 已删除`)
    await load()
  } catch {
    /* 拦截器已统一提示 */
  }
}

function showLog(row) {
  logContent.value = (row.error ? `【错误】${row.error}\n\n` : '') + (row.log_tail || '')
  logDialog.value = true
}

// ── 刷卡凭证 ──────────────────────────────────────────────
const voucherMonth = ref('')
const generating = ref(false)
const voucherBatches = ref([])
const expandedFiles = reactive({})
const fileSelection = reactive({})
const filePrinting = reactive({})
const mergePrinting = reactive({})
const defaultOutputDir = ref('')
const VOUCHER_DIR_KEYS = { dir: 'pos_voucher_statement_dir', out: 'pos_voucher_output_dir' }
const voucherStatementDir = ref('')
const voucherOutputDir = ref('')
const pickSource = ref(false)
const pickOutput = ref(false)

function lsGet(key) {
  try { return globalThis.localStorage?.getItem(key) || '' } catch { return '' }
}

function lsSet(key, value) {
  try { globalThis.localStorage?.setItem(key, value) } catch { /* 无 localStorage 环境忽略 */ }
}

async function loadVoucherConfig() {
  try {
    const { data } = await api.get('/finance/pos/vouchers/config')
    defaultOutputDir.value = data.configured_output_dir || data.default_output_dir || ''
  } catch {
    /* 目录默认值拿不到就留空，placeholder 走通用文案 */
  }
}

const expandedSet = () => new Set(Object.keys(expandedFiles).filter(k => expandedFiles[k]))

async function loadVouchers() {
  try {
    const { data } = await api.get('/finance/pos/vouchers')
    voucherBatches.value = (data.items || []).map(b => ({ ...b, _files: b.files || [] }))
    // 批次文件清单已更新：清掉旧的勾选/展开标记，避免对已不存在的文件发起合并
    for (const key of Object.keys(fileSelection)) delete fileSelection[key]
    for (const key of Object.keys(expandedFiles)) delete expandedFiles[key]
  } catch {
    voucherBatches.value = []
  }
}

async function generateVouchers() {
  if (!voucherMonth.value) return
  generating.value = true
  lsSet(VOUCHER_DIR_KEYS.dir, voucherStatementDir.value || '')
  lsSet(VOUCHER_DIR_KEYS.out, voucherOutputDir.value || '')
  try {
    const { data } = await api.post('/finance/pos/vouchers/generate', {
      month: voucherMonth.value,
      statement_dir: voucherStatementDir.value || '',
      output_dir: voucherOutputDir.value || '',
    }, { timeout: 600000 })
    if (data.status === 'ok') {
      ElMessage.success(`凭证生成完成：匹配 ${data.summary.matched ?? 0}/${data.summary.receipts_total ?? 0} 笔，文件 ${data.files.length} 个`)
    } else {
      ElMessage.error(`生成失败：${data.error || '未知原因'}`)
    }
    await loadVouchers()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    generating.value = false
  }
}

function onBatchExpand(row, expanded) {
  expandedFiles[row.id] = expanded.includes(row)
}

function onFileSelect(row, rows) {
  fileSelection[row.id] = rows.map(f => f.name)
}

function selectedFiles(row) {
  return fileSelection[row.id] || []
}

async function downloadFile(row, f) {
  try {
    const resp = await api.get(`/finance/pos/vouchers/${row.id}/files/${encodeURIComponent(f.name)}`, { responseType: 'blob' })
    saveBlob(resp.data, f.name)
  } catch {
    /* 拦截器已统一提示 */
  }
}

function closePrintWindow(win) {
  try {
    win.close()
  } catch {
    /* 页签已关闭或浏览器禁止脚本关窗 */
  }
}

async function printVoucherFile(row, f) {
  const key = `${row.id}:${f.name}`
  if (filePrinting[key]) return
  const win = openPrintWindow()
  if (!win) {
    ElMessage.warning('新标签页被浏览器拦截：请允许本站弹窗，或改用「下载」后在 PDF 阅读器中打印')
    return
  }
  filePrinting[key] = true
  try {
    const { data } = await api.get(
      `/finance/pos/vouchers/${row.id}/files/${encodeURIComponent(f.name)}`,
      { responseType: 'blob', timeout: 300000 },
    )
    if (!showPdfInWindow(data, { window: win })) throw new Error('无法在新标签页打开 PDF')
    ElMessage.info('正在新标签页加载 PDF；若未弹出打印对话框，请按 Ctrl+P，或改用「下载」')
  } catch {
    closePrintWindow(win)
  } finally {
    delete filePrinting[key]
  }
}

async function mergeDownload(row) {
  const names = selectedFiles(row)
  if (!names.length) return
  try {
    const { data } = await api.post(`/finance/pos/vouchers/${row.id}/merge`, { names }, { responseType: 'blob' })
    saveBlob(data, `刷卡凭证合并_${row.month}_${names.length}份.pdf`)
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function mergePrint(row) {
  const names = selectedFiles(row)
  if (!names.length || mergePrinting[row.id]) return
  const win = openPrintWindow()
  if (!win) {
    ElMessage.warning('新标签页被浏览器拦截：请允许本站弹窗，或改用「合并下载选中」后在 PDF 阅读器中打印')
    return
  }
  mergePrinting[row.id] = true
  try {
    const { data } = await api.post(
      `/finance/pos/vouchers/${row.id}/merge`,
      { names },
      { responseType: 'blob', timeout: 300000 },
    )
    if (!showPdfInWindow(data, { window: win })) throw new Error('无法在新标签页打开合并 PDF')
    ElMessage.info('正在新标签页加载合并 PDF；若未弹出打印对话框，请按 Ctrl+P，或改用「合并下载选中」')
  } catch {
    closePrintWindow(win)
  } finally {
    delete mergePrinting[row.id]
  }
}

onMounted(() => {
  voucherStatementDir.value = lsGet(VOUCHER_DIR_KEYS.dir)
  voucherOutputDir.value = lsGet(VOUCHER_DIR_KEYS.out)
  load(); loadVouchers(); loadVoucherConfig()
})
</script>

<style scoped>
.dir-cell {
  display: flex;
  gap: 8px;
  align-items: center;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.log {
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 12px;
}
</style>
