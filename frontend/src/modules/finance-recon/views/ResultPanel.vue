<template>
  <div class="panel">
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>勾稽检查</span>
          <span class="head-actions">
            <el-button size="small" :loading="summaryLoading" :disabled="!batch" @click="loadSummary">刷新摘要</el-button>
            <el-button size="small" type="primary" :loading="checking" :disabled="!batch || !canCheck" @click="runCheck">执行勾稽检查</el-button>
          </span>
        </div>
      </template>
      <p v-if="!batch" class="empty-hint">尚未选择批次；先在「对账批次」区选择一个批次。</p>
      <template v-else>
        <el-alert
          v-if="batch.status === 'failed' && batch.error_summary"
          :title="`上次检查失败：${batch.error_summary}`"
          type="error"
          :closable="false"
          class="gap-bottom"
        />
        <el-alert
          v-if="batch.needs_recheck"
          title="口径参数在上次勾稽检查通过后又被修改，摘要可能与口径不一致；请重新执行勾稽检查后再导出报告。"
          type="warning"
          :closable="false"
          class="gap-bottom"
        />
        <p v-if="!canCheck" class="muted">
          {{ checkBlockReason }}
        </p>
      </template>
    </el-card>

    <el-card v-if="batch" shadow="never" class="block">
      <template #header><span>勾稽结论（unknown = 待定：缺少口径或资料，不构成通过）</span></template>
      <el-table v-if="checkRows.length" :data="checkRows" size="small">
        <el-table-column prop="label" label="检查项" min-width="160" />
        <el-table-column prop="scope" label="范围" min-width="140" show-overflow-tooltip />
        <el-table-column label="结论" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.meta.type">{{ row.meta.label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="说明" min-width="320" show-overflow-tooltip />
        <el-table-column label="两侧/差异" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.left !== null || row.right !== null">{{ row.left ?? '—' }} vs {{ row.right ?? '—' }}<template v-if="row.delta">（差 {{ row.delta }}）</template></span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
      <p v-else class="empty-hint">暂无勾稽结论：执行检查或刷新摘要后展示。</p>
      <div v-if="unknownReasons.length" class="unknown-box">
        <p class="unknown-title">待定原因（{{ unknownReasons.length }} 项）</p>
        <ul class="unknown-list">
          <li v-for="(item, i) in unknownReasons" :key="i">
            【{{ item.label }}｜{{ item.scope }}】{{ item.reason }}
          </li>
        </ul>
      </div>
    </el-card>

    <el-card v-if="batch && summary" shadow="never" class="block">
      <template #header><span>合计与结算草稿</span></template>
      <el-descriptions :column="4" size="small" border>
        <el-descriptions-item label="账单累计应付">{{ totals.bill_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="欠费合计">{{ totals.arrear_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="核销应收合计">{{ totals.writeoff_receivable_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="已核销合计">{{ totals.writeoff_cleared_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="核销后欠费">{{ totals.writeoff_left_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="推算应收（核销+欠费）">{{ totals.estimated_receivable_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="认领收款合计">{{ totals.claim_recv_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="本次认领合计">{{ totals.claim_claimed_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="已确认抵扣">{{ totals.confirmed_credits_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="结算净额合计">{{ totals.settlement_net_total || '—' }}</el-descriptions-item>
        <el-descriptions-item label="违约金参考">{{ totals.penalty_reference_total || '未测算' }}</el-descriptions-item>
        <el-descriptions-item label="认领覆盖（确认/建议/未匹配）">
          {{ totals.matched_confirmed_total || '0.00' }} / {{ totals.matched_suggested_total || '0.00' }} / {{ totals.unmatched_claim_total || '0.00' }}
        </el-descriptions-item>
      </el-descriptions>
      <el-table v-if="summary.settlement && summary.settlement.length" :data="summary.settlement" size="small" class="gap-top" border>
        <el-table-column prop="contract_no" label="合同" min-width="140" />
        <el-table-column prop="arrear_total" label="当前欠费" width="110" align="right" />
        <el-table-column prop="due_receivable" label="已到期" width="100" align="right" />
        <el-table-column prop="undue_receivable" label="未到期" width="100" align="right" />
        <el-table-column prop="unmarked_receivable" label="未标注到期" width="110" align="right" />
        <el-table-column prop="confirmed_credits" label="确认抵扣" width="100" align="right" />
        <el-table-column prop="settlement_net" label="结算净额" width="110" align="right" />
        <el-table-column label="违约金（参考）" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.penalty_state === 'computed'">{{ row.penalty_reference }}</span>
            <el-tag v-else size="small" type="warning">{{ row.penalty_state === 'waived' ? '已豁免' : '未测算' }}</el-tag>
            <span class="muted"> {{ row.penalty_note }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="batch" shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>事实记录（标准化行）</span>
          <span class="head-actions">
            <el-input v-model="recordQuery.kind" placeholder="类别 writeoff/claim/arrear/bill" size="small" class="filter-input" clearable />
            <el-input v-model="recordQuery.contract" placeholder="合同号" size="small" class="filter-input" clearable />
            <el-input v-model="recordQuery.doc" placeholder="单据号" size="small" class="filter-input" clearable />
            <el-button size="small" type="primary" :loading="recordsLoading" @click="applyRecordFilters">查询</el-button>
          </span>
        </div>
      </template>
      <p class="muted legend">金额栏含义：核销=费用期应收/费用期核销/核销后欠费/预收款；认领=收款金额/本次认领/手续费；欠费=合计/固定租金/综管费；账单=累计应付。</p>
      <el-table :data="records" size="small" empty-text="暂无记录：先上传源文件">
        <el-table-column label="类别" width="80">
          <template #default="{ row }">{{ row.kind_label || row.kind }}</template>
        </el-table-column>
        <el-table-column prop="source_name" label="来源文件" min-width="150" show-overflow-tooltip />
        <el-table-column prop="row_no" label="源行" width="70" align="right" />
        <el-table-column prop="contract_no" label="合同" min-width="120" show-overflow-tooltip />
        <el-table-column prop="doc_no" label="单据号" min-width="130" show-overflow-tooltip />
        <el-table-column prop="fee_type" label="费用类型" min-width="110" show-overflow-tooltip />
        <el-table-column label="费用期" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.fee_period_start || '—') + ' ~ ' + (row.fee_period_end || '—') }}</template>
        </el-table-column>
        <el-table-column prop="occur_date" label="发生日" width="100" />
        <el-table-column label="金额1" width="90" align="right">
          <template #default="{ row }">{{ row.amount_1 ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="金额2" width="90" align="right">
          <template #default="{ row }">{{ row.amount_2 ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="金额3" width="90" align="right">
          <template #default="{ row }">{{ row.amount_3 ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="金额4" width="90" align="right">
          <template #default="{ row }">{{ row.amount_4 ?? '—' }}</template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="recordPage"
        :page-size="recordPageSize"
        :total="recordsTotal"
        layout="prev, pager, next, total"
        class="gap-top"
        @current-change="onRecordPageChange"
      />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { checkStateMeta, reconApi } from '../api'

const props = defineProps({ batch: { type: Object, default: null } })
const emit = defineEmits(['refresh'])

// 与后端 service.TRANSITIONS 对齐：draft/failed/ready → checking；checking 不能重入。
// ever_ready 只约束资料替换/删除（审计留痕），绝不拦截重跑——needs_recheck 只能靠重跑消除。
const canCheck = computed(() => {
  const b = props.batch
  return !!b && ['draft', 'failed', 'ready'].includes(b.status)
})
const checkBlockReason = computed(() => {
  const b = props.batch
  if (!b) return ''
  if (!['draft', 'failed', 'ready'].includes(b.status)) {
    return `批次当前状态「${b.status_label || b.status}」，不能发起检查。`
  }
  return ''
})

// ---------- 检查与摘要 ----------
const checking = ref(false)
const summary = ref(null)
const summaryLoading = ref(false)

async function loadSummary() {
  if (!props.batch) {
    summary.value = null
    return
  }
  summaryLoading.value = true
  try {
    const { data } = await reconApi.summary(props.batch.id)
    summary.value = data
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    summaryLoading.value = false
  }
}

async function runCheck() {
  if (!props.batch || checking.value) return
  checking.value = true
  try {
    const { data } = await reconApi.check(props.batch.id)
    ElMessage.success(`勾稽检查完成，批次状态：${data.status_label || data.status}`)
    emit('refresh')
    await loadSummary()
  } catch {
    /* 拦截器已统一提示（precheck 400 / 状态 409 / 引擎失败均带中文定位） */
  } finally {
    checking.value = false
  }
}

const checkRows = computed(() =>
  (summary.value?.checks || []).map((item) => ({ ...item, meta: checkStateMeta(item.state) })),
)
const unknownReasons = computed(() => summary.value?.unknown_reasons || [])
const totals = computed(() => summary.value?.totals || {})

// ---------- 事实记录分页 ----------
const records = ref([])
const recordsLoading = ref(false)
const recordsTotal = ref(0)
const recordPage = ref(1)
const recordPageSize = ref(20)
const recordQuery = reactive({ kind: '', contract: '', doc: '' })

function recordParams() {
  const params = { page: recordPage.value, page_size: recordPageSize.value }
  if (recordQuery.kind && recordQuery.kind.trim()) params.kind = recordQuery.kind.trim()
  if (recordQuery.contract && recordQuery.contract.trim()) params.contract = recordQuery.contract.trim()
  if (recordQuery.doc && recordQuery.doc.trim()) params.doc = recordQuery.doc.trim()
  return params
}

async function loadRecords() {
  if (!props.batch) {
    records.value = []
    recordsTotal.value = 0
    return
  }
  recordsLoading.value = true
  try {
    const { data } = await reconApi.records(props.batch.id, recordParams())
    records.value = data.items || []
    recordsTotal.value = Number(data.total) || 0
    recordPage.value = Number(data.page) || recordPage.value
    recordPageSize.value = Number(data.page_size) || recordPageSize.value
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    recordsLoading.value = false
  }
}

function applyRecordFilters() {
  recordPage.value = 1
  void loadRecords()
}

function onRecordPageChange(page) {
  recordPage.value = page
  void loadRecords()
}

// 首次挂载即拉取摘要与记录（页签 lazy，按 batch.id key 每次只挂一次；加载函数自带无批次守卫与 catch）
onMounted(() => {
  void loadSummary()
  void loadRecords()
})

defineExpose({
  canCheck,
  checking,
  summary,
  summaryLoading,
  loadSummary,
  runCheck,
  checkRows,
  unknownReasons,
  totals,
  records,
  recordsLoading,
  recordsTotal,
  recordPage,
  recordPageSize,
  recordQuery,
  loadRecords,
  applyRecordFilters,
  onRecordPageChange,
})
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.filter-input {
  width: 180px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.legend {
  margin: 0 0 8px;
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0 0 6px;
}
.gap-bottom {
  margin-bottom: 12px;
}
.gap-top {
  margin-top: 12px;
}
.unknown-box {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 6px;
  background: var(--el-color-warning-light-9);
}
.unknown-title {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
}
.unknown-list {
  margin: 0;
  padding-inline-start: 18px;
  font-size: 12px;
  line-height: 1.7;
}
</style>
