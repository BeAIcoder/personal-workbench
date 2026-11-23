<template>
  <div class="panel">
    <div class="toolbar">
      <el-button
        size="small"
        type="primary"
        :loading="running"
        :disabled="item.status !== 'draft'"
        title="按当前参数生成摊销测算（同输入幂等）"
        @click="runCalculation"
      >
        生成摊销测算
      </el-button>
      <el-button size="small" :loading="loading" @click="reload">刷新测算</el-button>
      <span v-if="item.status !== 'draft'" class="muted">项目已归档：不能再发起测算，可查看历史结果。</span>
      <span v-else class="muted">
        口径：净额 = 含税 ÷ (1 + 税率)；月摊 = 净额 ÷ 月数；末期尾差如实留在余额
      </span>
    </div>

    <el-table
      :data="runs"
      size="small"
      highlight-current-row
      empty-text="尚未测算：点「生成摊销测算」按当前参数生成月份级摊销表"
      @current-change="selectRun"
    >
      <el-table-column prop="id" label="批次" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="runStatusMeta(row.status).type">
            {{ row.status_label || runStatusMeta(row.status).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="校验" min-width="150">
        <template #default="{ row }">
          <span>通过 {{ row.check_counts.ok }} · 警告 {{ row.check_counts.warning }}</span>
          <span class="muted"> / 差异 {{ row.check_counts.failed }} · 未知 {{ row.check_counts.unknown }}</span>
        </template>
      </el-table-column>
      <el-table-column label="月摊" width="110" align="right">
        <template #default="{ row }">{{ formatMoney(row.summary.monthly_amount) }}</template>
      </el-table-column>
      <el-table-column label="尾差" width="100" align="right">
        <template #default="{ row }">{{ formatMoney(row.summary.rounding_difference) }}</template>
      </el-table-column>
      <el-table-column label="摊销区间" min-width="160">
        <template #default="{ row }">{{ row.summary.start_month }} ~ {{ row.summary.end_month }}</template>
      </el-table-column>
      <el-table-column label="标记" width="150">
        <template #default="{ row }">
          <el-tag v-if="row.stale" size="small" type="warning">已过期</el-tag>
          <el-tag v-if="row.has_warnings" size="small" type="warning" class="ml6">有风险</el-tag>
          <el-tag v-if="row.risk_acknowledged" size="small" type="info" class="ml6">风险已确认</el-tag>
          <span v-if="!row.stale && !row.has_warnings && !row.risk_acknowledged" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="rule_version" label="规则版本" min-width="130" show-overflow-tooltip />
    </el-table>

    <template v-if="selectedRun">
      <el-alert
        v-if="selectedStale"
        :title="STALE_HINT"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="含税金额">{{ formatMoney(selectedRun.summary.amount_incl_tax) }}</el-descriptions-item>
        <el-descriptions-item label="净额">{{ formatMoney(selectedRun.summary.amount_net) }}</el-descriptions-item>
        <el-descriptions-item label="税额">{{ formatMoney(selectedRun.summary.tax_amount) }}</el-descriptions-item>
        <el-descriptions-item label="月摊金额">{{ formatMoney(selectedRun.summary.monthly_amount) }}</el-descriptions-item>
        <el-descriptions-item label="摊销月数">{{ selectedRun.summary.months }}</el-descriptions-item>
        <el-descriptions-item label="摊销区间">
          {{ selectedRun.summary.start_month }} ~ {{ selectedRun.summary.end_month }}
        </el-descriptions-item>
        <el-descriptions-item label="累计摊销">{{ formatMoney(selectedRun.summary.amortized_total) }}</el-descriptions-item>
        <el-descriptions-item label="末期尾差">{{ formatMoney(selectedRun.summary.rounding_difference) }}</el-descriptions-item>
        <el-descriptions-item label="租赁到期日">{{ selectedRun.summary.lease_end_date }}</el-descriptions-item>
        <el-descriptions-item label="是否超租赁期">
          <el-tag v-if="selectedRun.summary.expiry_after_lease" size="small" type="warning">
            是（仅警告，未缩短年限）
          </el-tag>
          <span v-else>否</span>
        </el-descriptions-item>
        <el-descriptions-item label="校验结论" :span="2">
          <span>通过 {{ selectedRun.check_counts.ok }} · 警告 {{ selectedRun.check_counts.warning }}</span>
          <span class="muted"> / 差异 {{ selectedRun.check_counts.failed }} · 未知 {{ selectedRun.check_counts.unknown }}</span>
        </el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="selectedRun.summary.risk_flags && selectedRun.summary.risk_flags.length"
        type="warning"
        :closable="false"
        title="风险提示（确认前须显式确认）"
      >
        <ul class="risk-list">
          <li v-for="(flag, index) in selectedRun.summary.risk_flags" :key="index">{{ flag }}</li>
        </ul>
      </el-alert>

      <el-table
        :data="periods"
        size="small"
        max-height="420"
        empty-text="该批次没有月度摊销行（数据不足，不得视为已摊销）"
      >
        <el-table-column prop="period_month" label="月份" width="100" />
        <el-table-column prop="sequence" label="序号" width="70" align="right" />
        <el-table-column label="月摊金额" width="120" align="right">
          <template #default="{ row }">{{ formatMoney(row.monthly_amount) }}</template>
        </el-table-column>
        <el-table-column label="累计摊销" width="120" align="right">
          <template #default="{ row }">{{ formatMoney(row.cumulative) }}</template>
        </el-table-column>
        <el-table-column label="余额（净额−累计）" width="150" align="right">
          <template #default="{ row }">{{ formatMoney(row.balance) }}</template>
        </el-table-column>
        <el-table-column prop="remaining_months" label="剩余月数" width="100" align="right" />
      </el-table>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  STALE_HINT,
  asList,
  deferredApi,
  formatMoney,
  runStatusMeta,
} from '../api'

const props = defineProps({
  item: { type: Object, required: true },
})
const emit = defineEmits(['refresh', 'run-change'])

const runs = ref([])
const periods = ref([])
const selectedRun = ref(null)
const loading = ref(false)
const running = ref(false)

const selectedStale = computed(() => Boolean(selectedRun.value?.stale))

// 请求自身兜底：面板调用方不再重复 try/catch（守卫口径见 no-unhandled-request）
async function loadPeriods(runId) {
  try {
    const { data } = await deferredApi.periods(runId)
    periods.value = asList(data)
    return true
  } catch {
    /* 拦截器已统一提示 */
    periods.value = []
    return false
  }
}

async function selectRun(row) {
  if (!row) {
    selectedRun.value = null
    periods.value = []
    return
  }
  selectedRun.value = row
  if (await loadPeriods(row.id)) emit('run-change', row)
}

async function loadRuns() {
  loading.value = true
  try {
    const { data } = await deferredApi.runs(props.item.id)
    runs.value = asList(data)
    const current = runs.value.find((row) => row.id === selectedRun.value?.id)
    const target = current || runs.value[0] || null
    await selectRun(target)
    if (!target) {
      selectedRun.value = null
      periods.value = []
      emit('run-change', null)
    }
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

function reload() {
  return loadRuns()
}

async function runCalculation() {
  if (running.value) return
  if (props.item.status !== 'draft') {
    ElMessage.warning('项目已归档，不能发起测算')
    return
  }
  running.value = true
  try {
    const { data } = await deferredApi.createRun(props.item.id)
    ElMessage.success(`摊销测算已生成（批次 #${data.id}）`)
    await loadRuns()
    await selectRun(runs.value.find((row) => row.id === data.id) || data)
    emit('refresh')
  } catch {
    /* 拦截器已统一提示；非法参数/归档等由后端返回中文 409/400 */
  } finally {
    running.value = false
  }
}

onMounted(() => loadRuns())

defineExpose({
  props,
  runs,
  periods,
  selectedRun,
  loading,
  running,
  selectedStale,
  loadRuns,
  reload,
  selectRun,
  runCalculation,
  formatMoney,
  runStatusMeta,
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
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ml6 {
  margin-left: 6px;
}
.risk-list {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
}
</style>
