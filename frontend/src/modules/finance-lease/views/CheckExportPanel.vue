<template>
  <div class="panel">
    <el-card shadow="never" class="sub">
      <template #header>
        <div class="card-head">
          <span>校验结论</span>
          <span class="head-actions">
            <el-select
              v-model="selectedId"
              size="small"
              placeholder="选择测算批次"
              class="selector"
              :disabled="!calculations.length"
              @change="selectCalculation"
            >
              <el-option
                v-for="item in calculations"
                :key="item.id"
                :value="item.id"
                :label="`#${item.id} ${item.status_label || calculationStatusMeta(item.status).label}${item.stale ? '（已过期）' : '（当前）'}`"
              />
            </el-select>
            <el-button size="small" :loading="checksLoading" @click="reloadChecks">刷新校验</el-button>
          </span>
        </div>
      </template>

      <template v-if="activeCalculation">
        <el-alert v-if="isStale" :title="STALE_HINT" type="warning" :closable="false" class="mb8" />
        <div class="state-row">
          <span class="muted">总体结论</span>
          <el-tag size="small" :type="overallMeta.type">{{ overallMeta.label }}</el-tag>
          <el-tag size="small" type="success">通过 {{ counts.ok }}</el-tag>
          <el-tag size="small" type="danger">差异 {{ counts.failed }}</el-tag>
          <el-tag size="small" type="warning">未知 {{ counts.unknown }}</el-tag>
        </div>

        <el-table :data="checkRowsData" size="small" border empty-text="暂无校验结论：请先在「测算与期间表」生成测算">
          <el-table-column prop="title" label="校验项" min-width="150" />
          <el-table-column label="结论" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.meta.type">{{ row.meta.label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="期望" width="110">
            <template #default="{ row }">{{ valueText(row.expected) }}</template>
          </el-table-column>
          <el-table-column label="实际" width="110">
            <template #default="{ row }">{{ valueText(row.actual) }}</template>
          </el-table-column>
          <el-table-column label="差异" width="100">
            <template #default="{ row }">{{ valueText(row.difference) }}</template>
          </el-table-column>
          <el-table-column prop="message" label="说明" min-width="220" />
        </el-table>

        <template v-if="unknownChecks.length">
          <p class="hint">未知结论（数据不足，不能按 0 处理，也不能确认）：</p>
          <ul class="unknown-list">
            <li v-for="check in unknownChecks" :key="check.key">
              <b>{{ check.title }}</b>：{{ check.message || '缺少必要数据' }}
            </li>
          </ul>
        </template>

        <div class="actions">
          <el-tooltip :content="blockReason || '当前测算全部适用检查通过，可确认'" placement="top">
            <span>
              <el-button
                size="small"
                type="primary"
                :loading="confirming"
                :disabled="Boolean(blockReason)"
                @click="confirmCalculation"
              >
                确认测算
              </el-button>
            </span>
          </el-tooltip>
          <el-button size="small" :loading="exporting" :disabled="!activeCalculation" @click="exportXlsx">
            导出 xlsx 底稿
          </el-button>
          <span class="muted">
            确认后快照不可改写；过期或校验未全部通过时，当前导出会被后端拒绝（409），需显式按历史快照导出。
          </span>
        </div>
      </template>
      <p v-else class="empty-hint">还没有测算结果：请先到「测算与期间表」生成测算，再回来查看校验与导出。</p>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  STALE_HINT,
  asList,
  calculationStatusMeta,
  checkRows,
  checkStateMeta,
  confirmBlockReason,
  exportFileNameFromResponse,
  exportWarning,
  isStaleCalculation,
  leaseApi,
  needsAllowStale,
  normalizeChecks,
  normalizeCounts,
  saveBlob,
} from '../api'

const props = defineProps({
  contract: { type: Object, default: null },
  calculation: { type: Object, default: null },
})
const emit = defineEmits(['refresh', 'calculation'])

const calculations = ref([])
const selectedId = ref(props.calculation?.id ?? null)
const activeCalculation = ref(props.calculation || null)
const checks = ref(normalizeChecks(null))
const checksLoading = ref(false)
const confirming = ref(false)
const exporting = ref(false)

async function loadCalculations() {
  if (!props.contract?.id) return []
  try {
    const { data } = await leaseApi.calculations(props.contract.id)
    calculations.value = asList(data)
    return calculations.value
  } catch {
    /* 拦截器已统一提示 */
    return []
  }
}

async function loadChecksFor(calcId) {
  if (!calcId) return
  checksLoading.value = true
  try {
    const { data } = await leaseApi.checks(calcId)
    checks.value = normalizeChecks(data)
  } catch {
    // 校验接口失败不冒充通过：按未知展示，由拦截器给出中文原因
    checks.value = normalizeChecks(null)
  } finally {
    checksLoading.value = false
  }
}

async function selectCalculation(id) {
  const calcId = id && typeof id === 'object' ? id.id : id
  if (!calcId) return
  const cached = calculations.value.find((item) => item.id === calcId)
  if (cached) activeCalculation.value = cached
  try {
    const { data } = await leaseApi.calculation(calcId)
    activeCalculation.value = data
    selectedId.value = data.id
    emit('calculation', data)
    await loadChecksFor(data.id)
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function reloadChecks() {
  const id = activeCalculation.value?.id
  if (!id) return
  try {
    const { data } = await leaseApi.calculation(id)
    activeCalculation.value = data
  } catch {
    /* 拦截器已统一提示：刷新失败仍保留原视图 */
  }
  await loadChecksFor(id)
}

function pickDefaultId(list) {
  if (!Array.isArray(list) || !list.length) return null
  const current = list.find((item) => !item.stale)
  return (current || list[0]).id
}

async function loadInitial() {
  const list = await loadCalculations()
  if (props.calculation?.id) {
    activeCalculation.value = props.calculation
    selectedId.value = props.calculation.id
    await loadChecksFor(props.calculation.id)
    return
  }
  const id = pickDefaultId(list)
  if (id) await selectCalculation(id)
}

// 父组件在「测算与期间表」切换批次后同步过来（真实渲染下 props 为响应式）
watch(
  () => props.calculation?.id,
  (id) => {
    if (!id || activeCalculation.value?.id === id) return
    activeCalculation.value = props.calculation
    selectedId.value = id
    loadChecksFor(id)
  },
)

const isStale = computed(() => isStaleCalculation(activeCalculation.value))
const checkRowsData = computed(() => checkRows(checks.value.checks))
const unknownChecks = computed(() => checks.value.unknownChecks || [])
const overallMeta = computed(() => checkStateMeta(checks.value.state))

// 校验接口未返回计数时退回测算批次自带计数；两者都为空则视为未知（不可确认）
const counts = computed(() => {
  const fromChecks = checks.value.counts
  if (fromChecks.ok || fromChecks.failed || fromChecks.unknown) return fromChecks
  return normalizeCounts(activeCalculation.value)
})

const blockReason = computed(() => confirmBlockReason(activeCalculation.value, counts.value))

function valueText(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return Number.isFinite(num) ? num.toFixed(2) : String(value)
}

async function confirmCalculation() {
  const calc = activeCalculation.value
  if (!calc) return
  if (blockReason.value) {
    ElMessage.warning(blockReason.value)
    return
  }
  confirming.value = true
  try {
    const { data } = await leaseApi.confirm(calc.id)
    activeCalculation.value = { ...calc, ...(data || {}), status: data?.status || 'confirmed' }
    ElMessage.success('测算已确认：快照不可改写')
    emit('refresh')
  } catch {
    /* 409（输入已变化/校验未全部通过/已确认）由拦截器中文提示，视图保持原状态 */
  } finally {
    confirming.value = false
  }
}

async function exportXlsx() {
  const calc = activeCalculation.value
  if (!calc) return
  const allow = needsAllowStale(calc, counts.value)
  const warning = exportWarning(calc, counts.value)
  if (allow && warning) {
    try {
      await ElMessageBox.confirm(warning, '导出确认', { type: 'warning' })
    } catch {
      return
    }
  }
  exporting.value = true
  try {
    const response = await leaseApi.exportXlsx(calc.id, { allowStale: allow })
    const saved = saveBlob(response.data, exportFileNameFromResponse(response, props.contract, calc))
    if (saved === false) ElMessage.warning('当前浏览器未提供下载能力，请改用 Chrome/Edge 重试')
  } catch {
    /* 当前导出被 409 拒绝（过期/未全部通过）时由拦截器提示，不重复弹错 */
  } finally {
    exporting.value = false
  }
}

onMounted(loadInitial)

defineExpose({
  calculations,
  selectedId,
  activeCalculation,
  checks,
  checksLoading,
  confirming,
  exporting,
  loadCalculations,
  loadChecksFor,
  selectCalculation,
  reloadChecks,
  pickDefaultId,
  loadInitial,
  isStale,
  checkRowsData,
  unknownChecks,
  overallMeta,
  counts,
  blockReason,
  valueText,
  confirmCalculation,
  exportXlsx,
})
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sub {
  margin: 0;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.selector {
  width: 280px;
}
.state-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 13px;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 10px 0 6px;
}
.unknown-list {
  margin: 0;
  padding-left: 20px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0;
}
.mb8 {
  margin-bottom: 8px;
}
</style>
