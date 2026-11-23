<template>
  <div class="panel">
    <el-card shadow="never" class="sub">
      <template #header>
        <div class="card-head">
          <span>测算批次</span>
          <span class="head-actions">
            <el-button size="small" :loading="calculationsLoading" @click="loadCalculations">刷新</el-button>
            <el-button size="small" type="primary" :loading="creating" :disabled="!contract" @click="createCalculation">
              生成测算
            </el-button>
          </span>
        </div>
      </template>
      <p class="hint">
        同一合同 + 同一输入快照 + 同一规则版本会幂等返回已有批次，不重复堆积；输入变化后旧批次标记「已过期」，只能作为历史快照查看或显式导出。
      </p>
      <el-table :data="calculations" size="small" empty-text="暂无测算：先维护合同与付款计划，再点「生成测算」">
        <el-table-column prop="id" label="#" width="70" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="calculationStatusMeta(row.status).type">
              {{ row.status_label || calculationStatusMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="rule_version" label="规则版本" width="120" />
        <el-table-column label="测算时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="输入快照" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.stale" size="small" type="warning">已过期</el-tag>
            <el-tag v-else size="small" type="success">当前</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="检查" width="150">
          <template #default="{ row }">
            <span class="muted">
              通过 {{ countsOf(row).ok }} / 差异 {{ countsOf(row).failed }} / 未知 {{ countsOf(row).unknown }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="selectCalculation(row.id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <template v-if="activeCalculation">
      <el-alert v-if="isStale" :title="STALE_HINT" type="warning" :closable="false" class="sub" />
      <el-card shadow="never" class="sub">
        <template #header>
          <div class="card-head">
            <span>测算结果 #{{ activeCalculation.id }}</span>
            <span class="head-actions">
              <el-tag size="small" :type="calculationStatusMeta(activeCalculation.status).type">
                {{ activeCalculation.status_label || calculationStatusMeta(activeCalculation.status).label }}
              </el-tag>
              <el-tag v-if="isStale" size="small" type="warning">已过期（历史快照）</el-tag>
            </span>
          </div>
        </template>
        <div class="summary-grid">
          <div v-for="item in summaryRows" :key="item.label" class="summary-cell">
            <span class="summary-label">{{ item.label }}</span>
            <span class="summary-value">{{ item.value }}</span>
          </div>
        </div>
        <p class="hint">
          首期简化初始模型：使用权资产 = 租赁负债现值（不含预付租金、初始直接费用、复原成本、租赁激励）。
        </p>
      </el-card>

      <el-card shadow="never" class="sub">
        <template #header><span>评估表（付款期级：现值 / 折现因子 / 当期利息 U / 负债滚动）</span></template>
        <el-table :data="assessmentRows" size="small" border max-height="360">
          <el-table-column prop="sequence" label="期次" width="66" />
          <el-table-column prop="source_row" label="源行" width="66" />
          <el-table-column prop="period_start" label="计租开始" width="106" />
          <el-table-column prop="period_end" label="计租结束" width="106" />
          <el-table-column prop="months" label="月数" width="66" />
          <el-table-column label="付款额" width="110">
            <template #default="{ row }">{{ formatAmount(row.amount) }}</template>
          </el-table-column>
          <el-table-column label="期间利率" width="100">
            <template #default="{ row }">{{ formatRate6(row.period_rate) }}</template>
          </el-table-column>
          <el-table-column label="折现因子" width="118">
            <template #default="{ row }">{{ formatFactor(row.discount_factor) }}</template>
          </el-table-column>
          <el-table-column label="现值" width="110">
            <template #default="{ row }">{{ formatAmount(row.present_value) }}</template>
          </el-table-column>
          <el-table-column label="未确认融资费用" width="130">
            <template #default="{ row }">{{ formatAmount(row.unamortized_finance) }}</template>
          </el-table-column>
          <el-table-column label="当期利息 U" width="118">
            <template #default="{ row }">{{ formatAmount(row.interest_u) }}</template>
          </el-table-column>
          <el-table-column label="负债余额" width="118">
            <template #default="{ row }">{{ formatAmount(row.liability_balance) }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never" class="sub">
        <template #header><span>期间表（月份级：折旧 / 应付 / 利息；租赁付款为整期金额）</span></template>
        <el-table :data="periodRows" size="small" border max-height="360">
          <el-table-column prop="period_month" label="月份" width="96" />
          <el-table-column prop="sequence" label="对应付款期" width="100" />
          <el-table-column label="折旧" width="110">
            <template #default="{ row }">{{ formatAmount(row.depreciation) }}</template>
          </el-table-column>
          <el-table-column label="应付" width="110">
            <template #default="{ row }">{{ formatAmount(row.payable) }}</template>
          </el-table-column>
          <el-table-column label="利息" width="110">
            <template #default="{ row }">{{ formatAmount(row.interest) }}</template>
          </el-table-column>
          <el-table-column label="租赁付款（整期）" width="130">
            <template #default="{ row }">{{ formatAmount(row.lease_payment) }}</template>
          </el-table-column>
          <el-table-column label="未确认融资费用" width="130">
            <template #default="{ row }">{{ formatAmount(row.unamortized_finance) }}</template>
          </el-table-column>
          <el-table-column label="负债余额" width="118">
            <template #default="{ row }">{{ formatAmount(row.liability_balance) }}</template>
          </el-table-column>
        </el-table>
        <p class="hint">{{ PERIOD_PAYMENT_HINT }}</p>
      </el-card>
    </template>
    <el-card v-else shadow="never" class="sub">
      <p class="empty-hint">选择或生成一次测算后，这里展示评估表、期间表与汇总。</p>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  STALE_HINT,
  asList,
  calculationStatusMeta,
  isStaleCalculation,
  leaseApi,
  normalizeCounts,
} from '../api'

const props = defineProps({
  contract: { type: Object, default: null },
})
const emit = defineEmits(['refresh', 'calculation'])

const calculations = ref([])
const calculationsLoading = ref(false)
const activeCalculation = ref(null)
const assessmentRows = ref([])
const periodRows = ref([])
// 期间表的 lease_payment 是所属付款期的整期金额，期内每月重复显示，逐月求和会成倍放大
const PERIOD_PAYMENT_HINT =
  '「租赁付款（整期）」为所属付款期的整期金额，同一付款期内的每月行都会重复显示，不能逐月求和；折旧、利息、应付才是按月金额。'
const detailLoading = ref(false)
const creating = ref(false)

async function loadCalculations() {
  if (!props.contract?.id) return []
  calculationsLoading.value = true
  try {
    const { data } = await leaseApi.calculations(props.contract.id)
    calculations.value = asList(data)
    return calculations.value
  } catch {
    /* 拦截器已统一提示 */
    return []
  } finally {
    calculationsLoading.value = false
  }
}

async function selectCalculation(id) {
  if (!id) return
  detailLoading.value = true
  try {
    const [detail, assessment, periods] = await Promise.all([
      leaseApi.calculation(id),
      leaseApi.assessment(id),
      leaseApi.periods(id),
    ])
    activeCalculation.value = detail.data
    assessmentRows.value = asList(assessment.data)
    periodRows.value = asList(periods.data)
    emit('calculation', detail.data)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    detailLoading.value = false
  }
}

async function createCalculation() {
  if (!props.contract?.id || creating.value) return
  const knownIds = new Set(calculations.value.map((item) => item.id))
  creating.value = true
  try {
    const { data } = await leaseApi.createCalculation(props.contract.id)
    if (knownIds.has(data?.id)) {
      ElMessage.info('已存在相同输入快照的测算结果（幂等返回，未重复生成）')
    } else {
      ElMessage.success(`测算完成：#${data?.id ?? ''}`)
    }
    await loadCalculations()
    await selectCalculation(data?.id)
    emit('refresh')
  } catch {
    /* 拦截器已统一提示：付款计划为空/合同归档等由后端返回中文 400/409 */
  } finally {
    creating.value = false
  }
}

function pickDefaultId(list) {
  if (!Array.isArray(list) || !list.length) return null
  const current = list.find((item) => !item.stale)
  return (current || list[0]).id
}

const isStale = computed(() => isStaleCalculation(activeCalculation.value))

function countsOf(calc) {
  return normalizeCounts(calc)
}

const summaryRows = computed(() => {
  const summary = activeCalculation.value?.summary || {}
  const amount = (value) => (value === null || value === undefined ? '—' : formatAmount(value))
  return [
    { label: '租赁负债现值', value: amount(summary.present_value) },
    { label: '使用权资产初始', value: amount(summary.present_value) },
    { label: '利息合计', value: amount(summary.total_interest) },
    { label: '折旧合计', value: amount(summary.total_depreciation) },
    { label: '付款合计', value: amount(summary.total_payment) },
    { label: '付款期数', value: summary.payment_count ?? '—' },
    { label: '期间数（月）', value: summary.month_count ?? '—' },
    { label: '首期 / 末期', value: `${summary.first_period || '—'} / ${summary.last_period || '—'}` },
  ]
})

function formatAmount(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return Number.isFinite(num) ? num.toFixed(2) : '—'
}

function formatFactor(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return Number.isFinite(num) ? num.toFixed(9) : '—'
}

function formatRate6(value) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return Number.isFinite(num) ? num.toFixed(6) : '—'
}

function formatDateTime(value) {
  return String(value || '').slice(0, 16).replace('T', ' ')
}

async function loadInitial() {
  const list = await loadCalculations()
  const id = pickDefaultId(list)
  if (id) await selectCalculation(id)
}

onMounted(loadInitial)

defineExpose({
  calculations,
  calculationsLoading,
  activeCalculation,
  assessmentRows,
  periodRows,
  PERIOD_PAYMENT_HINT,
  detailLoading,
  creating,
  loadCalculations,
  selectCalculation,
  createCalculation,
  pickDefaultId,
  isStale,
  countsOf,
  summaryRows,
  formatAmount,
  formatFactor,
  formatRate6,
  formatDateTime,
  loadInitial,
  STALE_HINT,
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
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 0 0 8px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.summary-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
}
.summary-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.summary-value {
  font-size: 16px;
  font-weight: 600;
}
</style>
