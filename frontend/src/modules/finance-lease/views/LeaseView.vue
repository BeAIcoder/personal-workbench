<template>
  <div class="page">
    <HelpLink />
    <el-tabs v-model="pageTab" class="page-tabs">
      <el-tab-pane label="租赁合同" name="contracts">
        <div class="tab-body">
        <el-card shadow="never" class="block">
          <template #header>
            <div class="card-head">
              <span>租赁合同台账</span>
              <span class="head-actions">
                <el-button size="small" :loading="contractsLoading" @click="loadContracts">刷新</el-button>
                <el-button size="small" type="primary" @click="openCreate">新建合同</el-button>
              </span>
            </div>
          </template>
          <div class="summary-row">
            <span class="summary-item">合同数 <b>{{ summary.total_contracts ?? contracts.length }}</b></span>
            <span class="summary-item">草稿 <b>{{ summary.draft_count ?? 0 }}</b></span>
            <span class="summary-item">有未决校验 <b class="warn">{{ summary.contracts_with_pending ?? 0 }}</b></span>
            <span class="summary-note">口径：承租人简化初始模型「使用权资产 = 租赁负债现值」，不含预付租金/初始直接费用/复原成本</span>
          </div>
          <el-table
            :data="contracts"
            size="small"
            empty-text="暂无租赁合同：点右上角「新建合同」录入承租人、合同号、起止日与年折现率"
          >
            <el-table-column prop="contract_no" label="合同号" min-width="140" show-overflow-tooltip />
            <el-table-column prop="name" label="合同名称" min-width="120" show-overflow-tooltip />
            <el-table-column prop="lessee" label="承租人" min-width="130" show-overflow-tooltip />
            <el-table-column prop="project" label="项目/资产组" min-width="120" show-overflow-tooltip />
            <el-table-column label="租赁期间" min-width="180">
              <template #default="{ row }">{{ row.start_date || '—' }} ~ {{ row.end_date || '—' }}</template>
            </el-table-column>
            <el-table-column label="年折现率" width="100">
              <template #default="{ row }">{{ formatRate(row.annual_rate) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="contractStatusMeta(row.status).type">
                  {{ row.status_label || contractStatusMeta(row.status).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最近测算" min-width="150">
              <template #default="{ row }">
                <template v-if="row.latest_calculation_id">
                  <el-tag size="small" :type="calculationStatusMeta(row.latest_calculation_status).type">
                    {{ calculationStatusMeta(row.latest_calculation_status).label }}
                  </el-tag>
                  <el-tag v-if="row.latest_calculation_stale" size="small" type="warning" class="ml6">已过期</el-tag>
                  <span class="muted"> #{{ row.latest_calculation_id }}</span>
                </template>
                <span v-else class="muted">未测算</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="190" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="selectContract(row)">选择</el-button>
                <el-button
                  v-if="row.status === 'draft'"
                  size="small"
                  link
                  title="归档后合同与付款计划只读，已有测算仍可查看"
                  @click="archiveContract(row)"
                >
                  归档
                </el-button>
                <el-button size="small" link type="danger" @click="removeContract(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <template v-if="selected">
          <el-alert v-if="selectedStale" :title="STALE_HINT" type="warning" :closable="false" class="block" />
          <el-card shadow="never" class="block">
            <template #header>
              <div class="card-head">
                <span class="contract-title">
                  {{ selected.contract_no }}<template v-if="selected.name">（{{ selected.name }}）</template>
                  · {{ selected.lessee || '未填承租人' }} · {{ selected.start_date }} ~ {{ selected.end_date }}
                </span>
                <span class="head-actions">
                  <el-tag size="small" :type="contractStatusMeta(selected.status).type">
                    {{ selected.status_label || contractStatusMeta(selected.status).label }}
                  </el-tag>
                  <el-button size="small" :loading="refreshing" @click="refreshContract">刷新合同</el-button>
                </span>
              </div>
            </template>
            <el-tabs v-model="activeTab">
              <el-tab-pane label="合同与付款" name="contract" lazy>
                <ContractPanel :key="`contract-${selected.id}`" :contract="selected" @refresh="refreshContract" />
              </el-tab-pane>
              <el-tab-pane label="测算与期间表" name="measure" lazy>
                <MeasurePanel
                  :key="`measure-${selected.id}`"
                  :contract="selected"
                  @refresh="refreshContract"
                  @calculation="onCalculation"
                />
              </el-tab-pane>
              <el-tab-pane label="校验与导出" name="checks" lazy>
                <CheckExportPanel
                  :key="`checks-${selected.id}`"
                  :contract="selected"
                  :calculation="selectedCalculation"
                  @refresh="refreshContract"
                  @calculation="onCalculation"
                />
              </el-tab-pane>
            </el-tabs>
          </el-card>
        </template>
        <el-card v-else shadow="never" class="block">
          <p class="empty-hint">
            从上方选择一个合同后，这里展示「合同与付款 / 测算与期间表 / 校验与导出」三个工作区。
          </p>
        </el-card>
        </div>
      </el-tab-pane>
      <el-tab-pane label="BIP对账" name="reconciliation" lazy>
        <ReconciliationPanel />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="createVisible" title="新建租赁合同（草稿）" width="580px">
      <el-form label-width="110px" @submit.prevent>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="合同号" required>
              <el-input v-model="createForm.contract_no" placeholder="内部唯一合同号" maxlength="64" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="合同名称">
              <el-input v-model="createForm.name" placeholder="如 办公场地租赁（可选）" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="承租人主体" required>
              <el-input v-model="createForm.lessee" placeholder="承租人/核算主体" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="项目/资产组">
              <el-input v-model="createForm.project" placeholder="项目或资产组（可选）" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="出租方">
              <el-input v-model="createForm.lessor" placeholder="出租方名称（可选）" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="年折现率" required>
              <el-input v-model="createForm.annual_rate" placeholder="小数，例如 3.5% 填 0.035" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="租赁起始日" required>
              <el-date-picker
                v-model="createForm.start_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="起始日"
                class="full-width"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="租赁截止日" required>
              <el-date-picker
                v-model="createForm.end_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="截止日"
                class="full-width"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="利率依据">
          <el-input v-model="createForm.rate_basis" placeholder="如 增量借款利率/合同利率（可选）" maxlength="2000" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.remark" type="textarea" :rows="2" maxlength="2000" placeholder="租赁范围、特殊约定（可选）" />
        </el-form-item>
        <el-form-item label="附件依据">
          <el-input v-model="createForm.attachment_note" placeholder="合同扫描件/台账位置说明（可选）" maxlength="2000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="createLoading" :disabled="!canCreate" @click="createContract">
          创建草稿
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import HelpLink from '../../../components/HelpLink.vue'
import {
  STALE_HINT,
  asList,
  buildContractPayload,
  calculationStatusMeta,
  contractStatusMeta,
  emptyContractForm,
  leaseApi,
} from '../api'
import ContractPanel from './ContractPanel.vue'
import MeasurePanel from './MeasurePanel.vue'
import CheckExportPanel from './CheckExportPanel.vue'
import ReconciliationPanel from './ReconciliationPanel.vue'

// ---------- 页面页签：租赁合同工作区 / BIP 只读对账（不依赖选中单个合同） ----------
const pageTab = ref('contracts')

// ---------- 合同台账 ----------
const contracts = ref([])
const contractsLoading = ref(false)
const summary = ref({})
const selected = ref(null)
const selectedCalculation = ref(null)
const activeTab = ref('contract')
const refreshing = ref(false)

async function loadContracts() {
  contractsLoading.value = true
  try {
    const { data } = await leaseApi.contracts({ page: 1, page_size: 200 })
    contracts.value = asList(data)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    contractsLoading.value = false
  }
}

async function loadSummary() {
  try {
    const { data } = await leaseApi.summary()
    summary.value = data || {}
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function loadAll() {
  await loadContracts()
  await loadSummary()
}

async function selectContract(rowOrId) {
  const id = rowOrId && typeof rowOrId === 'object' ? rowOrId.id : rowOrId
  if (!id) return
  try {
    const { data } = await leaseApi.contract(id)
    selected.value = data
    selectedCalculation.value = null
    activeTab.value = 'contract'
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function refreshContract() {
  if (!selected.value) return
  const id = selected.value.id
  refreshing.value = true
  try {
    const { data } = await leaseApi.contract(id)
    selected.value = data
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    refreshing.value = false
  }
}

function onCalculation(calc) {
  selectedCalculation.value = calc || null
}

const selectedStale = computed(() => Boolean(selected.value?.latest_calculation_stale))

function formatRate(value) {
  if (value === null || value === undefined || value === '') return '—'
  const rate = Number(value)
  if (!Number.isFinite(rate)) return '—'
  return `${(rate * 100).toFixed(2)}%`
}

async function archiveContract(row) {
  try {
    await ElMessageBox.confirm(
      `确认归档合同「${row.contract_no}」？归档后合同与付款计划只读，已有测算仍可查看。`,
      '归档确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await leaseApi.patchContract(row.id, { status: 'archived' })
    ElMessage.success('合同已归档（只读）')
    await loadAll()
    if (selected.value?.id === row.id) await selectContract(row.id)
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function removeContract(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除合同「${row.contract_no}」？存在测算历史的合同后端会拒绝删除（409），请改为归档。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await leaseApi.deleteContract(row.id)
    ElMessage.success('合同已删除')
    if (selected.value?.id === row.id) {
      selected.value = null
      selectedCalculation.value = null
    }
    await loadAll()
  } catch {
    /* 拦截器已统一提示 */
  }
}

// ---------- 新建合同 ----------
const createVisible = ref(false)
const createLoading = ref(false)
const createForm = reactive(emptyContractForm())

const canCreate = computed(
  () =>
    Boolean(String(createForm.contract_no || '').trim()) &&
    Boolean(String(createForm.lessee || '').trim()) &&
    Boolean(createForm.start_date) &&
    Boolean(createForm.end_date) &&
    createForm.annual_rate !== null &&
    createForm.annual_rate !== '',
)

function openCreate() {
  Object.assign(createForm, emptyContractForm())
  createVisible.value = true
}

async function createContract() {
  if (createLoading.value) return
  let payload
  try {
    payload = buildContractPayload(createForm)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  createLoading.value = true
  try {
    const { data } = await leaseApi.createContract(payload)
    ElMessage.success(`合同「${data.contract_no || payload.contract_no}」已创建（草稿）`)
    createVisible.value = false
    await loadAll()
    await selectContract(data.id)
  } catch {
    /* 拦截器已统一提示；合同号重复等由后端返回中文 409/400 */
  } finally {
    createLoading.value = false
  }
}

onMounted(loadAll)

defineExpose({
  pageTab,
  contracts,
  contractsLoading,
  summary,
  selected,
  selectedCalculation,
  activeTab,
  refreshing,
  loadContracts,
  loadSummary,
  loadAll,
  selectContract,
  refreshContract,
  onCalculation,
  selectedStale,
  formatRate,
  archiveContract,
  removeContract,
  createVisible,
  createLoading,
  createForm,
  canCreate,
  openCreate,
  createContract,
  STALE_HINT,
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.tab-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.block {
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
.contract-title {
  font-weight: 600;
}
.summary-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  margin-bottom: 10px;
  font-size: 13px;
}
.summary-item {
  color: var(--el-text-color-regular);
}
.summary-note {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.warn {
  color: var(--el-color-warning);
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ml6 {
  margin-left: 6px;
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0;
}
.full-width {
  width: 100%;
}
</style>
