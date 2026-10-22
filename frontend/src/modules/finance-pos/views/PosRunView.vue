<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>建行 POS 收款对账</span>
          <el-button size="small" type="primary" @click="dialog.visible = true">新建运行</el-button>
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
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'uploaded'" size="small" link type="primary" :loading="runningId === row.id" @click="execute(row)">执行</el-button>
            <el-button size="small" link type="primary" :disabled="!row.artifacts.length" @click="downloadZip(row)">打包下载</el-button>
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
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'
import { takeFiles } from '../../_shared/fileInput'

const loading = ref(false)
const items = ref([])
const runningId = ref(null)
const dialog = reactive({ visible: false })
const creating = ref(false)
const form = reactive({ files: [] })
const logDialog = ref(false)
const logContent = ref('')

const STATUS_TEXT = { uploaded: '待执行', running: '执行中', ok: '已完成', failed: '失败' }
const STATUS_TYPE = { uploaded: 'info', running: 'warning', ok: 'success', failed: 'danger' }
const statusText = (s) => STATUS_TEXT[s] || s
const statusType = (s) => STATUS_TYPE[s] || 'info'

// 与后端 service.MAX_UPLOAD_BYTES / MAX_BATCH_BYTES 对齐：上传前本地预检，
// 超限文件直接跳过并提示，避免必然被后端 400 的无效上传
const MAX_FILE_BYTES = 50 * 1024 * 1024
const MAX_TOTAL_BYTES = 100 * 1024 * 1024
const fmtMB = (bytes) => `${Math.round(bytes / 1024 / 1024)}MB`

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

async function execute(row) {
  runningId.value = row.id
  try {
    const { data } = await api.post(`/finance/pos/runs/${row.id}/execute`, {}, { timeout: 600000 })
    ElMessage.success(data.status === 'ok' ? `执行完成，产物 ${data.artifacts.length} 个` : `执行失败：${data.error}`)
    await load()
  } catch {
    /* 拦截器已统一提示 */
    await load()
  } finally {
    runningId.value = null
  }
}

function downloadZip(row) {
  window.open(`/api/finance/pos/runs/${row.id}/download`, '_blank')
}

function showLog(row) {
  logContent.value = (row.error ? `【错误】${row.error}\n\n` : '') + (row.log_tail || '')
  logDialog.value = true
}

onMounted(load)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hint {
  color: #909399;
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
