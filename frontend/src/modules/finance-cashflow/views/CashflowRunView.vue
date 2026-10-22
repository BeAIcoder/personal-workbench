<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>现金流滚动预测自动填报</span>
          <el-button size="small" type="primary" @click="openDialog">新建运行</el-button>
        </div>
      </template>
      <el-alert type="info" :closable="false" show-icon
        title="上传银行辅助明细账、预测模板、上月上报版等（或打一个 ZIP），由本机引擎自动回填「X月实际」列，产物可打包下载。引擎不可用时接口会提示。" />
      <el-table :data="items" size="small" style="margin-top: 12px" empty-text="暂无运行记录">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="actual_month" label="回填月份" width="90" align="center" />
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
      <el-pagination
        :current-page="page"
        :page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        class="cashflow-pager"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </el-card>

    <el-dialog v-model="dialog.visible" title="新建现金流填报运行" width="520px">
      <el-form label-width="90px">
        <el-form-item label="回填月份">
          <el-select v-model="form.actual_month" style="width: 140px">
            <el-option v-for="m in 12" :key="m" :label="`${m} 月`" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="输入文件">
          <input type="file" multiple accept=".xlsx,.xls,.csv,.zip" @change="onFiles" />
          <div class="hint">银行辅助明细账 + 预测模板（必填）；上月上报版 / 凭证列表 / M03 按需；可打 ZIP</div>
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
import { currentMonth } from '../month'

const loading = ref(false)
const items = ref([])
// 列表请求序号：快速翻页/改页大小时丢弃慢的旧响应，避免旧列表覆盖新列表
let loadSeq = 0
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const runningId = ref(null)
const dialog = reactive({ visible: false })
const creating = ref(false)
// 回填月份口径为 1-12（后端 Form(ge=1, le=12)）；getMonth() 返回 0-11，
// 直接用它会让 1 月提交 0 被 422 拒绝、其余月份整体偏小一个月。
const form = reactive({ actual_month: currentMonth(), files: [] })
const logDialog = ref(false)
const logContent = ref('')

// 每次打开新建对话框都取当前月，避免沿用上次选择/上月残留
function openDialog() {
  form.actual_month = currentMonth()
  form.files = []
  dialog.visible = true
}
const STATUS_TEXT = { uploaded: '待执行', running: '执行中', ok: '已完成', failed: '失败' }
const STATUS_TYPE = { uploaded: 'info', running: 'warning', ok: 'success', failed: 'danger' }
const statusText = (s) => STATUS_TEXT[s] || s
const statusType = (s) => STATUS_TYPE[s] || 'info'

async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const { data } = await api.get('/finance/cashflow/runs', {
      params: { page: page.value, page_size: pageSize.value },
    })
    if (seq !== loadSeq) return
    items.value = data.items || []
    total.value = data.total || 0
  } catch {
    if (seq !== loadSeq) return
    items.value = []
    total.value = 0
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function onPageChange(p) {
  page.value = p
  load()
}
function onSizeChange(s) {
  pageSize.value = s
  page.value = 1
  load()
}

function onFiles(e) {
  form.files = takeFiles(e)
}

async function create() {
  if (!form.files.length) return
  creating.value = true
  try {
    const fd = new FormData()
    fd.append('actual_month', form.actual_month)
    for (const f of form.files) fd.append('files', f)
    const { data } = await api.post('/finance/cashflow/runs', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    ElMessage.success(`运行 #${data.id} 已创建，点「执行」开始填报`)
    dialog.visible = false
    form.files = []
    page.value = 1
    await load()  } catch {
    /* 拦截器已统一提示 */
  } finally {
    creating.value = false
  }
}

async function execute(row) {
  runningId.value = row.id
  try {
    const { data } = await api.post(`/finance/cashflow/runs/${row.id}/execute`, {}, { timeout: 600000 })
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
  window.open(`/api/finance/cashflow/runs/${row.id}/download`, '_blank')
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

.cashflow-pager {
  margin-top: 12px;
  justify-content: flex-end;
}

.log {
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 12px;
}
</style>
