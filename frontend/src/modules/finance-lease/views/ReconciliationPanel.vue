<template>
  <div class="panel">
    <el-card shadow="never" class="sub">
      <template #header>
        <div class="card-head">
          <span>BIP 科目余额只读对账</span>
          <span class="head-actions">
            <el-button size="small" :loading="contextLoading" @click="loadContext">刷新上下文</el-button>
            <el-button
              size="small"
              type="primary"
              :loading="checking"
              :disabled="Boolean(blockReason)"
              :title="blockReason || '按承租人 / 账簿 / 期间对账五个科目位'"
              @click="runCheck"
            >
              执行对账
            </el-button>
          </span>
        </div>
      </template>

      <el-alert
        :title="READONLY_HINT"
        type="info"
        :closable="false"
        class="mb8"
      />

      <el-form :inline="true" size="small" class="filters" @submit.prevent>
        <el-form-item label="承租人">
          <el-select
            v-model="form.lessee"
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入承租人主体"
            class="filter-select"
            @change="loadMappings"
          >
            <el-option v-for="item in context.lessees" :key="item" :value="item" :label="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="核算账簿">
          <el-select
            v-model="form.ledger"
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入 BIP 核算账簿"
            class="filter-select"
            @change="loadMappings"
          >
            <el-option v-for="item in context.ledgers" :key="item" :value="item" :label="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="期间">
          <el-select
            v-model="form.period"
            filterable
            allow-create
            default-first-option
            placeholder="YYYY-MM"
            class="filter-select"
          >
            <el-option v-for="item in context.periods" :key="item" :value="item" :label="item" />
          </el-select>
        </el-form-item>
      </el-form>
      <p class="hint">
        承租人取自租赁合同，账簿/期间取自 BIP 科目余额表的真实取值；无法对账时后端返回「未知」并给出中文原因，不会按 0 通过。
      </p>
    </el-card>

    <el-card shadow="never" class="sub">
      <template #header>
        <div class="card-head">
          <span>科目映射（承租人 × 账簿，共五类科目位）</span>
          <span class="head-actions">
            <span class="muted">未填写的科目位视为未配置</span>
            <el-button size="small" type="primary" :loading="saving" @click="saveMappings">
              保存映射
            </el-button>
          </span>
        </div>
      </template>
      <el-table :data="mappingRows" size="small" border empty-text="暂无科目位">
        <el-table-column prop="label" label="科目位" min-width="190" />
        <el-table-column label="BIP 科目编码" min-width="170">
          <template #default="{ row }">
            <el-input
              v-model="row.subject_code"
              size="small"
              maxlength="50"
              placeholder="如 1801"
              @change="onMappingChanged"
            />
          </template>
        </el-table-column>
        <el-table-column label="余额方向" width="130">
          <template #default="{ row }">
            <el-select v-model="row.direction" size="small" @change="onMappingChanged">
              <el-option
                v-for="item in directions"
                :key="item.value"
                :value="item.value"
                :label="item.label"
              />
            </el-select>
          </template>
        </el-table-column>
      </el-table>
      <p class="hint">映射只写入本模块的科目对照表，绝不对 BIP 凭证/余额做任何写入。</p>
    </el-card>

    <el-card shadow="never" class="sub">
      <template #header>
        <div class="card-head">
          <span>对账结果</span>
          <span v-if="result" class="head-actions">
            <el-tag size="small" :type="result.meta.type">{{ result.meta.label }}</el-tag>
            <el-tag size="small" type="success">一致 {{ result.counts.ok }}</el-tag>
            <el-tag size="small" type="danger">差异 {{ result.counts.failed }}</el-tag>
            <el-tag size="small" type="warning">未知 {{ result.counts.unknown }}</el-tag>
            <span class="muted">容差 {{ result.tolerance || RECON_TOLERANCE }}</span>
          </span>
        </div>
      </template>

      <template v-if="result">
        <div class="state-row">
          <span class="muted">
            {{ result.lessee }} · {{ result.ledger }} · {{ result.period }} ·
            合同 {{ result.coverage?.contract_count ?? 0 }} 个（已采用
            {{ result.coverage?.selected_count ?? 0 }} / 未知
            {{ result.coverage?.unknown_count ?? 0 }}）
          </span>
        </div>

        <el-table :data="result.subjects" size="small" border empty-text="暂无对账科目">
          <el-table-column prop="subject_key_label" label="科目位" min-width="180" />
          <el-table-column label="BIP 科目" width="120">
            <template #default="{ row }">
              {{ row.subject_code || '—' }}
              <span v-if="row.direction_label" class="muted">/{{ row.direction_label }}</span>
            </template>
          </el-table-column>
          <el-table-column label="模块期望" width="120">
            <template #default="{ row }">{{ formatAmount(row.expected) }}</template>
          </el-table-column>
          <el-table-column label="BIP 实际" width="120">
            <template #default="{ row }">{{ formatAmount(row.actual) }}</template>
          </el-table-column>
          <el-table-column label="差异" width="110">
            <template #default="{ row }">{{ formatAmount(row.difference) }}</template>
          </el-table-column>
          <el-table-column label="结论" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.meta.type">{{ row.meta.label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源" min-width="200">
            <template #default="{ row }">
              <span class="muted">{{ row.source_label || row.source || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="说明" min-width="220" />
        </el-table>

        <template v-if="result.unknownSubjects.length">
          <p class="hint">未知项（数据不足，不能当成 0 或「一致」）：</p>
          <ul class="reason-list">
            <li v-for="row in result.unknownSubjects" :key="row.subject_key">
              <b>{{ row.subject_key_label }}</b>：{{ row.reason }}
            </li>
          </ul>
        </template>

        <template v-if="result.coverage?.reasons?.length">
          <p class="hint">合同覆盖问题（后端原样返回）：</p>
          <ul class="reason-list">
            <li v-for="(reason, index) in result.coverage.reasons" :key="index">{{ reason }}</li>
          </ul>
        </template>

        <p class="hint">合同覆盖明细</p>
        <el-table :data="result.contracts" size="small" border empty-text="该承租人下暂无租赁合同">
          <el-table-column prop="contract_no" label="合同号" min-width="130" />
          <el-table-column prop="name" label="合同名称" min-width="120" />
          <el-table-column label="采用" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="coverageMeta(row).type">
                {{ coverageMeta(row).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="测算批次" width="120">
            <template #default="{ row }">
              <template v-if="row.calculation_id">
                #{{ row.calculation_id }}
                <span v-if="row.stale" class="muted">已过期</span>
              </template>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="原值" width="100">
            <template #default="{ row }">{{ formatAmount(row.present_value) }}</template>
          </el-table-column>
          <el-table-column label="累计折旧" width="105">
            <template #default="{ row }">{{ formatAmount(row.accumulated_depreciation) }}</template>
          </el-table-column>
          <el-table-column label="剩余付款" width="105">
            <template #default="{ row }">{{ formatAmount(row.remaining_payment) }}</template>
          </el-table-column>
          <el-table-column label="未确认" width="100">
            <template #default="{ row }">{{ formatAmount(row.unrecognized_finance) }}</template>
          </el-table-column>
          <el-table-column label="净负债" width="100">
            <template #default="{ row }">{{ formatAmount(row.liability_balance) }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="未采用原因" min-width="220" />
        </el-table>
      </template>
      <p v-else class="empty-hint">
        选择承租人、核算账簿与期间后点「执行对账」；每个合同只采用「已确认 + 当前输入 + 覆盖该期间」的最新测算批次。
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  RECON_DIRECTIONS,
  RECON_TOLERANCE,
  buildMappingsPayload,
  coverageMeta,
  emptyMappingRows,
  formatAmount,
  leaseApi,
  mappingsToRows,
  normalizeReconcile,
  reconCheckBlockReason,
} from '../api'

const READONLY_HINT =
  '只读对账：BIP 余额/凭证只被读取，不写任何 BIP 数据；本页只保存「租赁核算 → BIP 科目」的映射，差异/未知一律显式返回，不按 0 通过。'

const contextLoading = ref(false)
const checking = ref(false)
const saving = ref(false)
const context = ref({ lessees: [], ledgers: [], periods: [], mappings: [] })
const directions = RECON_DIRECTIONS
const form = reactive({ lessee: '', ledger: '', period: '' })
const mappingRows = ref(emptyMappingRows())
const result = ref(null)

const blockReason = computed(() => reconCheckBlockReason(form))

async function loadContext() {
  contextLoading.value = true
  try {
    const { data } = await leaseApi.reconciliationContext()
    context.value = data || { lessees: [], ledgers: [], periods: [], mappings: [] }
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    contextLoading.value = false
  }
}

async function loadMappings() {
  const lessee = String(form.lessee || '').trim()
  const ledger = String(form.ledger || '').trim()
  // 未选全范围时清空编辑器：不沿用上一个承租人/账簿的科目，避免串号
  if (!lessee || !ledger) {
    mappingRows.value = emptyMappingRows()
    return
  }
  try {
    const { data } = await leaseApi.reconciliationMappings({ lessee, ledger })
    mappingRows.value = mappingsToRows(data?.items || [])
  } catch {
    /* 拦截器已统一提示 */
  }
}

function onMappingChanged() {
  // 映射未保存时结果可能已过期：提示重新对账，不自动改写结果
  if (result.value) result.value = null
}

async function saveMappings() {
  let payload
  try {
    payload = buildMappingsPayload({ lessee: form.lessee, ledger: form.ledger, rows: mappingRows.value })
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  saving.value = true
  try {
    const { data } = await leaseApi.putReconciliationMappings(payload)
    mappingRows.value = mappingsToRows(data?.items || [])
    ElMessage.success(`已保存 ${data?.total ?? 0} 条科目映射（仅本模块，未写 BIP）`)
  } catch {
    /* 拦截器已统一提示；非法科目位/重复科目由后端返回 4xx 中文 */
  } finally {
    saving.value = false
  }
}

async function runCheck() {
  const blocked = reconCheckBlockReason(form)
  if (blocked) {
    ElMessage.warning(blocked)
    return
  }
  checking.value = true
  try {
    const { data } = await leaseApi.reconciliationCheck({
      lessee: String(form.lessee).trim(),
      ledger: String(form.ledger).trim(),
      period: String(form.period).trim(),
    })
    result.value = normalizeReconcile(data)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    checking.value = false
  }
}

onMounted(loadContext)

defineExpose({
  READONLY_HINT,
  contextLoading,
  checking,
  saving,
  context,
  directions,
  form,
  mappingRows,
  result,
  blockReason,
  loadContext,
  loadMappings,
  onMappingChanged,
  saveMappings,
  runCheck,
  RECON_TOLERANCE,
  coverageMeta,
  formatAmount,
})
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
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
.filters {
  margin-bottom: 4px;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 6px 0 0;
}
.state-row {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
}
.reason-list {
  margin: 4px 0 10px 18px;
  padding: 0;
  color: var(--el-color-warning);
  font-size: 12px;
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.filter-select {
  width: 240px;
}
.mb8 {
  margin-bottom: 8px;
}
</style>
