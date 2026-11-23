<template>
  <div class="panel">
    <el-alert
      v-if="!run"
      type="info"
      :closable="false"
      title="请先在「测算与月度表」选择或生成一次测算，再查看校验结论、确认与导出。"
    />

    <template v-else>
      <el-alert
        v-if="run.stale"
        :title="STALE_HINT"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="warningCount > 0"
        :title="RISK_HINT"
        type="warning"
        :closable="false"
        show-icon
      />

      <div class="toolbar">
        <el-tag :type="runStatusMeta(run.status).type" size="small">
          {{ run.status_label || runStatusMeta(run.status).label }}
        </el-tag>
        <el-tag size="small" type="info">批次 #{{ run.id }}</el-tag>
        <el-tag v-if="run.has_warnings" size="small" type="warning">警告 {{ warningCount }}</el-tag>
        <el-button size="small" :loading="loading" @click="reload">刷新校验</el-button>
        <el-button
          size="small"
          type="primary"
          :loading="confirming"
          :disabled="Boolean(blockReason) || Boolean(riskBlockReason)"
          :title="blockReason || riskBlockReason || '确认后快照不可改写'"
          @click="confirmRun"
        >
          确认测算
        </el-button>
        <el-button size="small" :loading="exporting" @click="exportWorkbook">导出底稿 xlsx</el-button>
        <span v-if="blockReason" class="muted">{{ blockReason }}</span>
        <span v-else-if="riskBlockReason" class="muted">{{ riskBlockReason }}</span>
      </div>

      <el-form v-if="warningCount > 0" label-width="120px" class="risk-form">
        <el-form-item label="已确认风险">
          <el-checkbox v-model="riskAcknowledged" :disabled="run.status === 'confirmed'">
            我已阅读风险提示并接受（不缩短年限、不吸收尾差）
          </el-checkbox>
        </el-form-item>
        <el-form-item label="风险说明">
          <el-input
            v-model="riskNote"
            type="textarea"
            :rows="2"
            maxlength="2000"
            :disabled="run.status === 'confirmed'"
            placeholder="写明接受该风险的依据与审批（确认时必填）"
          />
        </el-form-item>
      </el-form>

      <el-descriptions v-if="run.risk_acknowledged" :column="2" border size="small">
        <el-descriptions-item label="风险已确认">是</el-descriptions-item>
        <el-descriptions-item label="风险说明">{{ run.risk_note || '—' }}</el-descriptions-item>
      </el-descriptions>

      <div class="checks-head">
        <span>校验结论</span>
        <span class="muted">
          通过 {{ counts.ok }} · 警告 {{ counts.warning }} · 差异 {{ counts.failed }} · 未知 {{ counts.unknown }}
        </span>
      </div>
      <el-table
        :data="rows"
        size="small"
        empty-text="没有校验结论（未知，不得视为通过）：请重新测算"
      >
        <el-table-column prop="title" label="检查项" min-width="200" show-overflow-tooltip />
        <el-table-column label="结论" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.meta.type">{{ row.meta.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="期望值" width="130" align="right">
          <template #default="{ row }">{{ formatMoney(row.expected) }}</template>
        </el-table-column>
        <el-table-column label="实际值" width="130" align="right">
          <template #default="{ row }">{{ formatMoney(row.actual) }}</template>
        </el-table-column>
        <el-table-column label="差异" width="110" align="right">
          <template #default="{ row }">{{ formatMoney(row.difference) }}</template>
        </el-table-column>
        <el-table-column prop="message" label="说明" min-width="280" show-overflow-tooltip />
      </el-table>

      <p class="muted">
        导出规则：过期或存在差异/未知时后端拒绝（409），历史快照需显式允许后导出；警告不阻断导出，
        但会如实写入底稿「校验」页。
      </p>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  RISK_HINT,
  STALE_HINT,
  checkRows,
  confirmBlockReason,
  deferredApi,
  exportFileNameFromResponse,
  exportWarning,
  formatMoney,
  needsAllowStale,
  normalizeChecks,
  normalizeCounts,
  riskNoteBlockReason,
  runStatusMeta,
  saveBlob,
} from '../api'

const props = defineProps({
  item: { type: Object, required: true },
  run: { type: Object, default: null },
})
const emit = defineEmits(['refresh', 'run-change'])

const loading = ref(false)
const confirming = ref(false)
const exporting = ref(false)
const riskAcknowledged = ref(false)
const riskNote = ref('')
const checksData = ref(null)

// 校验刷新结果只覆盖 checks/check_counts/stale，其余字段仍以传入的测算为准
const activeRun = computed(() => {
  if (!props.run) return null
  if (!checksData.value) return props.run
  return {
    ...props.run,
    checks: checksData.value.checks || props.run.checks,
    check_counts: checksData.value.check_counts || props.run.check_counts,
    stale: checksData.value.stale ?? props.run.stale,
  }
})
const counts = computed(() => normalizeCounts(activeRun.value))
const warningCount = computed(() => counts.value.warning)
const rows = computed(() => checkRows(activeRun.value?.checks || []))
const blockReason = computed(() => confirmBlockReason(activeRun.value, counts.value))
const riskBlockReason = computed(() =>
  riskNoteBlockReason(activeRun.value, counts.value, riskAcknowledged.value, riskNote.value),
)

watch(
  () => props.run?.id,
  () => {
    checksData.value = null
    riskAcknowledged.value = Boolean(props.run?.risk_acknowledged)
    riskNote.value = props.run?.risk_note || ''
  },
  { immediate: true },
)

async function reload() {
  if (!props.run) return
  loading.value = true
  try {
    const { data } = await deferredApi.checks(props.run.id)
    checksData.value = data || null
    if (data) {
      const normalized = normalizeChecks(data)
      if (!normalized.counts.warning && !props.run.risk_acknowledged) {
        riskAcknowledged.value = false
        riskNote.value = ''
      }
    }
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

async function confirmRun() {
  const run = activeRun.value
  if (!run) {
    ElMessage.warning('请先在「测算与月度表」选择一次测算结果')
    return
  }
  const reason = blockReason.value || riskBlockReason.value
  if (reason) {
    ElMessage.warning(reason)
    return
  }
  confirming.value = true
  try {
    const { data } = await deferredApi.confirm(run.id, {
      risk_acknowledged: riskAcknowledged.value,
      risk_note: String(riskNote.value || '').trim(),
    })
    ElMessage.success('测算已确认（快照不可改写）')
    emit('run-change', data)
    emit('refresh')
    await reload()
  } catch {
    /* 拦截器已统一提示；过期/风险未确认等由后端返回中文 409 */
  } finally {
    confirming.value = false
  }
}

async function exportWorkbook() {
  const run = activeRun.value
  if (!run) {
    ElMessage.warning('请先在「测算与月度表」选择一次测算结果')
    return
  }
  const allowStale = needsAllowStale(run, counts.value)
  const warning = exportWarning(run, counts.value)
  if (warning) {
    try {
      await ElMessageBox.confirm(warning, allowStale ? '历史快照导出' : '风险提示导出', {
        type: 'warning',
      })
    } catch {
      return
    }
  }
  exporting.value = true
  try {
    const response = await deferredApi.exportXlsx(run.id, { allowStale })
    const filename = exportFileNameFromResponse(response, props.item, run)
    saveBlob(response.data, filename)
    ElMessage.success('摊销底稿已导出')
  } catch {
    /* 拦截器已统一提示；409 未通过校验/过期由后端返回中文原因 */
  } finally {
    exporting.value = false
  }
}

defineExpose({
  props,
  loading,
  confirming,
  exporting,
  riskAcknowledged,
  riskNote,
  checksData,
  activeRun,
  counts,
  warningCount,
  rows,
  blockReason,
  riskBlockReason,
  reload,
  confirmRun,
  exportWorkbook,
  formatMoney,
  runStatusMeta,
  saveBlob,
  RISK_HINT,
  STALE_HINT,
})
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.checks-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.risk-form {
  padding: 8px 12px 0;
  border: 1px dashed var(--el-color-warning);
  border-radius: 4px;
}
</style>
