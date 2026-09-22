<template>
  <div class="page">
    <div class="toolbar">
      <div class="title-block">
        <div class="page-title">定时任务</div>
        <div class="page-sub">到点自动执行 Agent 任务，结果写入笔记</div>
      </div>
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建任务
      </el-button>
    </div>

    <el-table :data="rows" v-loading="loading" stripe>
      <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
      <el-table-column label="提示词" min-width="220">
        <template #default="{ row }">
          <el-tooltip :content="row.prompt" placement="top" :show-after="300">
            <span class="prompt-cell">{{ snippet(row.prompt, 40) }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="执行专家" width="130">
        <template #default="{ row }">{{ agentLabel(row.agent_name, agents) }}</template>
      </el-table-column>
      <el-table-column label="调度方式" width="120">
        <template #default="{ row }">{{ formatSchedule(row) }}</template>
      </el-table-column>
      <el-table-column label="启用" width="80" align="center">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="(val) => toggleEnabled(row, val)" />
        </template>
      </el-table-column>
      <el-table-column label="最近运行" width="170">
        <template #default="{ row }">
          <span>{{ fmtDT(row.last_run_at) }}</span>
          <el-tag :type="runStatusTag[row.last_status]" size="small" effect="plain" style="margin-left: 6px">
            {{ runStatusLabel[row.last_status] || row.last_status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近运行摘要" min-width="220">
        <template #default="{ row }">
          <el-tooltip v-if="row.recent_runs?.length" :content="row.recent_runs[0].summary" placement="top" :show-after="300">
            <span class="prompt-cell">{{ snippet(row.recent_runs[0].summary, 40) }}</span>
          </el-tooltip>
          <span v-else class="text-muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="success" plain :loading="runningId === row.id" @click="runNow(row)">立即运行</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无定时任务，点击右上角新建" :image-size="80" />
      </template>
    </el-table>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑定时任务' : '新建定时任务'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="100" show-word-limit placeholder="例如：每周税种测算提醒" />
        </el-form-item>
        <el-form-item label="提示词" prop="prompt">
          <el-input
            v-model="form.prompt"
            type="textarea"
            :rows="4"
            maxlength="5000"
            show-word-limit
            placeholder="到点后交给 Agent 执行的内容，例如：汇总本周新增进项发票并核对税率"
          />
        </el-form-item>
        <el-form-item label="执行专家">
          <el-select v-model="form.agent_name" placeholder="智能路由（不指定则由 AI 自动选择专家）" clearable style="width: 100%">
            <el-option v-for="a in agents" :key="a.name" :label="agentOptionLabel(a)" :value="a.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="工作模式">
          <el-select v-model="form.mode" style="width: 100%">
            <el-option label="标准执行" value="standard" />
            <el-option label="只读咨询" value="readonly" />
            <el-option label="深度研究" value="deep" />
          </el-select>
        </el-form-item>
        <el-form-item label="调度类型">
          <el-radio-group v-model="form.schedule_type">
            <el-radio-button label="interval">间隔执行</el-radio-button>
            <el-radio-button label="daily">每天定时</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.schedule_type === 'interval'" label="执行间隔" prop="interval_minutes">
          <el-input-number v-model="form.interval_minutes" :min="5" :max="10080" :step="1" style="width: 100%" />
          <div class="form-hint">每 5 ~ 10080 分钟执行一次</div>
        </el-form-item>
        <el-form-item v-if="form.schedule_type === 'daily'" label="执行时间" prop="daily_at">
          <el-time-picker
            v-model="form.daily_at"
            format="HH:mm"
            value-format="HH:mm"
            placeholder="选择每天执行的时间"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { jobApi, assistantApi } from '../api'
import { fmtDT, snippet } from '../utils/format'
import { agentLabel, agentOptionLabel, formatSchedule, runStatusLabel, runStatusTag } from '../utils/jobs'

const rows = ref([])
const agents = ref([])
const loading = ref(false)
const runningId = ref(null)

const formRef = ref(null)
const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({
  name: '',
  prompt: '',
  agent_name: null,
  mode: 'standard',
  schedule_type: 'interval',
  interval_minutes: 60,
  daily_at: '09:00',
  enabled: true,
})

function validateInterval(_rule, value, callback) {
  if (form.schedule_type !== 'interval') return callback()
  if (!value) return callback(new Error('请输入执行间隔分钟数'))
  callback()
}
function validateDaily(_rule, value, callback) {
  if (form.schedule_type !== 'daily') return callback()
  if (!value || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) return callback(new Error('请选择执行时间（HH:MM）'))
  callback()
}
const rules = {
  name: [{ required: true, message: '请输入任务名称', trigger: 'blur' }],
  prompt: [{ required: true, message: '请输入到点执行的提示词', trigger: 'blur' }],
  interval_minutes: [{ validator: validateInterval, trigger: 'change' }],
  daily_at: [{ validator: validateDaily, trigger: 'change' }],
}

// 请求序号：连续刷新时丢弃过期响应
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const { data } = await jobApi.list()
    if (seq !== loadSeq) return  // 已有更新的请求发出，丢弃旧结果
    rows.value = data
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadAgents() {
  try {
    const { data } = await assistantApi.agents.list()
    agents.value = data.agents || []
  } catch {
    // 专家列表拉取失败不阻塞页面，下拉退化为仅「智能路由」
  }
}

function resetForm() {
  Object.assign(form, {
    name: '',
    prompt: '',
    agent_name: null,
    mode: 'standard',
    schedule_type: 'interval',
    interval_minutes: 60,
    daily_at: '09:00',
    enabled: true,
  })
}

function openCreate() {
  dialog.id = null
  resetForm()
  dialog.visible = true
}

function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, {
    name: row.name,
    prompt: row.prompt,
    agent_name: row.agent_name,
    mode: row.mode,
    schedule_type: row.schedule_type,
    interval_minutes: row.interval_minutes ?? 60,
    daily_at: row.daily_at || '09:00',
    enabled: row.enabled,
  })
  dialog.visible = true
}

async function save() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  const payload = {
    name: form.name.trim(),
    prompt: form.prompt.trim(),
    agent_name: form.agent_name || null,
    mode: form.mode,
    schedule_type: form.schedule_type,
    interval_minutes: form.schedule_type === 'interval' ? form.interval_minutes : null,
    daily_at: form.schedule_type === 'daily' ? form.daily_at : null,
    enabled: form.enabled,
  }
  dialog.saving = true
  try {
    if (dialog.id) {
      await jobApi.update(dialog.id, payload)
      ElMessage.success('任务已保存')
    } else {
      await jobApi.create(payload)
      ElMessage.success('任务创建成功')
    }
    dialog.visible = false
    load()
  } finally {
    dialog.saving = false
  }
}

async function toggleEnabled(row, val) {
  try {
    await jobApi.update(row.id, { enabled: val })
    ElMessage.success(val ? '任务已启用' : '任务已停用')
    load()
  } catch {
    row.enabled = !val  // 启停失败回滚开关
  }
}

async function runNow(row) {
  runningId.value = row.id
  try {
    const { data } = await jobApi.runNow(row.id)
    if (data.status === 'ok') ElMessage.success('已完成，结果已写入笔记')
    else if (data.status === 'skipped') ElMessage.warning(data.summary || '已跳过本次执行')
    else ElMessage.error(data.summary || '执行失败')
    load()
  } finally {
    runningId.value = null
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确定删除定时任务「${row.name}」吗？删除后不可恢复。`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  await jobApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(() => {
  load()
  loadAgents()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
  gap: 12px;
  flex-wrap: wrap;
}

.title-block {
  min-width: 0;
}

.page-title {
  font-size: 17px;
  font-weight: 600;
}

.page-sub {
  color: #909399;
  font-size: 12px;
  margin-top: 2px;
}

.prompt-cell {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

.text-muted {
  color: #909399;
}

.form-hint {
  color: #909399;
  font-size: 12px;
  line-height: 1.4;
}
</style>
