<template>
  <div class="page" v-loading="loading">
    <HelpLink section="6.13" />
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>经营财务报表监控看板</span>
          <div>
            <el-button size="small" :loading="running" @click="run">运行管线刷新</el-button>
            <AskAiButton :prompt="aiPrompt" />
            <el-select v-model="selected" placeholder="选择快照" size="small" style="width: 200px; margin-left: 8px" @change="loadHtml">
              <el-option v-for="s in items" :key="s.id" :label="`${s.stamp}（${s.status === 'ok' ? '成功' : '失败'}）`" :value="s.id" :disabled="s.status !== 'ok'" />
            </el-select>
          </div>
        </div>
      </template>
      <div v-if="running" class="run-progress">
        <el-progress :percentage="100" :indeterminate="true" :duration="3" :show-text="false" />
        <span class="run-progress-text">{{ runStatus }}</span>
      </div>
      <el-empty v-if="!items.length" description="暂无快照，点右上角「运行管线刷新」生成（首次运行需数秒）" />
      <iframe v-else-if="htmlUrl" :src="htmlUrl" class="board-frame" frameborder="0" sandbox="allow-scripts" title="经营财务报表看板快照" />
      <el-alert v-else type="info" :closable="false" title="请选择一个成功快照查看" />
    </el-card>

    <el-card v-if="items.length" shadow="never" style="margin-top: 16px">
      <template #header><span>快照记录</span></template>
      <el-table :data="items" size="small">
        <el-table-column prop="stamp" label="时间戳" width="180" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">
              {{ row.status === 'ok' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="elapsed_sec" label="耗时(秒)" width="100" align="right" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" :disabled="row.status !== 'ok'" @click="selected = row.id; loadHtml()">查看</el-button>
            <el-button v-if="row.log_tail" size="small" link type="warning" @click="showLog(row)">日志</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="logDialog" title="运行日志" width="680px">
      <pre class="log">{{ logContent }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import HelpLink from '../../../components/HelpLink.vue'
import api from '../../../api'
import { isTerminalRunStatus, shouldReloadAfterRun } from '../utils'
import AskAiButton from '../../../components/assistant/AskAiButton.vue'

// 异步运行契约（#4）：POST /finance/board/runs → 202 {run_id,status}，再轮询
// GET /finance/board/runs/{run_id}；旧后端无该路由（404）时回退同步 POST /run。
// 正常路径每请求都短超时，前端不再挂一个 1500s 的阻塞请求。
const POLL_INTERVAL_MS = 2000
const POLL_REQUEST_TIMEOUT_MS = 20000
// 异步提交只是登记任务，必须快速返回；慢就是异常，不能变成新的长请求
const SUBMIT_TIMEOUT_MS = 60000
// 后端最坏预算：2 次尝试 660s + 重试 5s + 残留锁等待 60s = 1385s；留少量余量
const POLL_BUDGET_MS = 1400000

const loading = ref(false)
const running = ref(false)
const runStatus = ref('')
const items = ref([])
const selected = ref(null)
const logDialog = ref(false)
const logContent = ref('')

// 轮询代次：卸载/重新运行后旧循环自行退出，不再写状态
let pollToken = 0

const aiPrompt = computed(() => '请解读最近一次报表看板快照：核心指标变化与异常点')

const htmlUrl = computed(() => (selected.value ? `${api.defaults.baseURL}/finance/board/snapshots/${selected.value}/html` : ''))

function loadHtml() { /* htmlUrl 计算属性自动刷新 iframe */ }

// 失败快照的管线日志（log_tail，后端已按尾部有界截断）；空值给占位文案，
// <pre> 纯文本插值自动转义，不渲染原始 HTML。
function showLog(row) {
  logContent.value = row.log_tail || '暂无日志'
  logDialog.value = true
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/board/snapshots')
    items.value = data.items || []
    const latestOk = items.value.find((s) => s.status === 'ok')
    if (latestOk && !selected.value) selected.value = latestOk.id
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// 终态提示与选中快照：以后端快照 id 或 ok/failed 为准；running/pending 等继续轮询
async function afterRun(data) {
  // 兼容后端可能的结果包装：{snapshot:{...}} / {result:{...}} / 直接快照字段
  const payload = data?.snapshot || data?.result || data || {}
  const snapshotId = payload.snapshot_id ?? payload.id ?? null
  const failed = String(payload.status || '').toLowerCase() === 'failed'

  // 先刷新列表：异步结果只带 snapshot_id，stamp 要从中补；失败快照也立刻可见
  await load()
  let stamp = payload.stamp || ''
  if (snapshotId) {
    selected.value = snapshotId
    if (!stamp) stamp = items.value.find((s) => s.id === snapshotId)?.stamp || ''
  } else if (payload.stamp) {
    const hit = items.value.find((s) => s.stamp === payload.stamp && s.status === 'ok')
    if (hit) selected.value = hit.id
  }

  const elapsed = payload.elapsed_sec == null ? '—' : String(Math.round(Number(payload.elapsed_sec)))
  if (failed) {
    ElMessage.warning(`管线运行失败（${elapsed}s）：${payload.error || '失败快照已归档'}`)
  } else {
    ElMessage.success(`运行完成（${elapsed}s），已归档快照 ${stamp || '—'}`)
  }
}

async function pollRun(runId, token) {
  const deadline = Date.now() + POLL_BUDGET_MS
  while (Date.now() < deadline) {
    if (token !== pollToken) return
    // 运行记录失效（服务重启后内存中的 run 丢失）走 404 分支，不弹全局错误
    let resp
    try {
      resp = await api.get(`/finance/board/runs/${encodeURIComponent(runId)}`, {
        timeout: POLL_REQUEST_TIMEOUT_MS,
        validateStatus: (s) => (s >= 200 && s < 300) || s === 404,
      })
    } catch {
      // 拦截器已统一提示（网络中断/5xx）：停止本轮等待，保留现有快照列表
      return
    }
    const { status, data } = resp
    if (status === 404) {
      ElMessage.warning('运行记录已失效（服务可能重启过），请查看快照列表')
      await load()
      return
    }
    if (isTerminalRunStatus(data?.status) || data?.snapshot_id) {
      await afterRun(data)
      return
    }
    const waited = data?.elapsed_sec ?? (Date.now() - deadline + POLL_BUDGET_MS) / 1000
    runStatus.value = `管线运行中（已等待 ${Math.round(waited)}s）`
    await sleep(POLL_INTERVAL_MS)
  }
  if (token === pollToken) {
    ElMessage.warning('管线仍在运行，已停止等待；稍后刷新快照列表即可看到结果')
    await load()
  }
}

async function runSync() {
  try {
    // 同步回退是单次阻塞请求，超时必须覆盖后端最坏预算 1385s（INV-009：
    // check_contracts / test_engineering_contracts 直接解析这里的数字，不要改成常量）
    const { data } = await api.post('/finance/board/run', {}, { timeout: 1500000 })
    await afterRun(data)
  } catch (err) {
    // 拦截器已统一提示；管线失败仍会归档 failed 快照，重拉列表让其可见
    if (shouldReloadAfterRun(err)) await load()
  }
}

async function run() {
  if (running.value) return
  const token = ++pollToken
  running.value = true
  runStatus.value = '正在提交运行请求…'
  try {
    const { status, data } = await api.post('/finance/board/runs', {}, {
      timeout: SUBMIT_TIMEOUT_MS,
      // 旧后端没有 /runs：放行 404/405 以便静默回退同步 /run，不弹一次误导性的错误提示
      validateStatus: (s) => (s >= 200 && s < 300) || s === 404 || s === 405,
    })
    if (status === 404 || status === 405) {
      runStatus.value = '当前后端不支持异步运行，改为同步等待…'
      await runSync()
      return
    }
    if (data && data.run_id) {
      runStatus.value = '管线运行中（已提交）'
      await pollRun(data.run_id, token)
      return
    }
    // 兼容：后端直接返回了快照结果（同步语义）
    await afterRun(data)
  } catch (err) {
    // 拦截器已统一提示；管线失败仍会归档 failed 快照，需重拉列表让其可见
    if (shouldReloadAfterRun(err)) await load()
  } finally {
    if (token === pollToken) {
      running.value = false
      runStatus.value = ''
    }
  }
}

onBeforeUnmount(() => { pollToken += 1 })

onMounted(load)
</script>

<style scoped>
.board-frame {
  width: 100%;
  /* 跟随视口高度，避免页面套小窗再内部滚动 */
  height: calc(100vh - 240px);
  min-height: 560px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.run-progress {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.run-progress .el-progress {
  flex: 1;
}

.run-progress-text {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.log {
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 12px;
}
</style>
