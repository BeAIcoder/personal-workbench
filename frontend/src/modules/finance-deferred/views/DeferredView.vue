<template>
  <div class="page">
    <HelpLink />
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>长期待摊项目台账（租入资产改良支出）</span>
          <span class="head-actions">
            <el-button size="small" :loading="itemsLoading" @click="loadAll">刷新</el-button>
            <el-button size="small" type="primary" @click="openCreate">新建项目</el-button>
          </span>
        </div>
      </template>
      <div class="summary-row">
        <span class="summary-item">项目数 <b>{{ summary.total_items ?? items.length }}</b></span>
        <span class="summary-item">草稿 <b>{{ summary.draft_count ?? 0 }}</b></span>
        <span class="summary-item">已归档 <b>{{ summary.archived_count ?? 0 }}</b></span>
        <span class="summary-item">有未决校验 <b class="warn">{{ summary.items_with_pending ?? 0 }}</b></span>
        <span class="summary-note">
          口径：净额 = 含税 ÷ (1 + 税率)；月摊 = 净额 ÷ 摊销月数（两位四舍五入）；末期尾差如实保留
        </span>
      </div>
      <el-table
        :data="items"
        size="small"
        empty-text="暂无长期待摊项目：点右上角「新建项目」录入含税金额、税率、起摊月、摊销月数与租赁到期日"
      >
        <el-table-column prop="project_code" label="项目编码" min-width="130" show-overflow-tooltip />
        <el-table-column prop="name" label="项目名称" min-width="130" show-overflow-tooltip />
        <el-table-column prop="project" label="所属项目" min-width="110" show-overflow-tooltip />
        <el-table-column label="含税金额" width="120" align="right">
          <template #default="{ row }">{{ formatMoney(row.amount_incl_tax) }}</template>
        </el-table-column>
        <el-table-column label="净额" width="120" align="right">
          <template #default="{ row }">{{ formatMoney(row.amount_net) }}</template>
        </el-table-column>
        <el-table-column label="税率" width="90">
          <template #default="{ row }">{{ formatRate(row.tax_rate) }}</template>
        </el-table-column>
        <el-table-column label="摊销区间" min-width="160">
          <template #default="{ row }">{{ row.start_month || '—' }} ~ {{ row.amortization_end_month || '—' }}</template>
        </el-table-column>
        <el-table-column label="月数" width="70" align="right">
          <template #default="{ row }">{{ row.months }}</template>
        </el-table-column>
        <el-table-column label="租赁到期日" width="120">
          <template #default="{ row }">{{ row.lease_end_date || '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="itemStatusMeta(row.status).type">
              {{ row.status_label || itemStatusMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近测算" min-width="150">
          <template #default="{ row }">
            <template v-if="row.latest_run_id">
              <el-tag size="small" :type="runStatusMeta(row.latest_run_status).type">
                {{ runStatusMeta(row.latest_run_status).label }}
              </el-tag>
              <el-tag v-if="row.latest_run_stale" size="small" type="warning" class="ml6">已过期</el-tag>
              <span class="muted"> #{{ row.latest_run_id }}</span>
            </template>
            <span v-else class="muted">未测算</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="selectItem(row)">选择</el-button>
            <el-button
              v-if="row.status === 'draft'"
              size="small"
              link
              title="归档后项目与测算入口只读，已有测算仍可查看与导出"
              @click="archiveItem(row)"
            >
              归档
            </el-button>
            <el-button size="small" link type="danger" @click="removeItem(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <template v-if="selected">
      <el-alert v-if="selectedStale" :title="STALE_HINT" type="warning" :closable="false" class="block" />
      <el-card shadow="never" class="block">
        <template #header>
          <div class="card-head">
            <span class="item-title">
              {{ selected.project_code }}<template v-if="selected.name">（{{ selected.name }}）</template>
              · {{ selected.project || '未填所属项目' }} · {{ selected.start_month }} ~ {{ selected.amortization_end_month }}
            </span>
            <span class="head-actions">
              <el-tag size="small" :type="itemStatusMeta(selected.status).type">
                {{ selected.status_label || itemStatusMeta(selected.status).label }}
              </el-tag>
              <el-button size="small" :loading="refreshing" @click="refreshItem">刷新项目</el-button>
            </span>
          </div>
        </template>
        <el-tabs v-model="activeTab">
          <el-tab-pane label="参数维护" name="params" lazy>
            <ItemPanel :key="`params-${selected.id}`" :item="selected" @refresh="refreshItem" />
          </el-tab-pane>
          <el-tab-pane label="测算与月度表" name="schedule" lazy>
            <SchedulePanel
              :key="`schedule-${selected.id}`"
              :item="selected"
              @refresh="refreshItem"
              @run-change="onRunChange"
            />
          </el-tab-pane>
          <el-tab-pane label="风险/确认/导出" name="risk" lazy>
            <RiskExportPanel
              :key="`risk-${selected.id}`"
              :item="selected"
              :run="selectedRun"
              @refresh="refreshItem"
              @run-change="onRunChange"
            />
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </template>
    <el-card v-else shadow="never" class="block">
      <p class="empty-hint">
        从上方选择一个项目后，这里展示「参数维护 / 测算与月度表 / 风险/确认/导出」三个工作区。
      </p>
    </el-card>

    <el-dialog v-model="createVisible" title="新建长期待摊项目（草稿）" width="640px">
      <el-form label-width="110px" @submit.prevent>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="项目编码" required>
              <el-input v-model="createForm.project_code" placeholder="内部唯一编码" maxlength="64" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="项目名称" required>
              <el-input v-model="createForm.name" placeholder="如 门店装修改良" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="所属项目" required>
              <el-input v-model="createForm.project" placeholder="项目/资产组" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="含税金额" required>
              <el-input v-model="createForm.amount_incl_tax" placeholder="含税总额，两位小数" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="税率" required>
              <el-input v-model="createForm.tax_rate" placeholder="小数，例如 6% 填 0.06" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="摊销月数" required>
              <el-input-number v-model="createForm.months" :min="1" :max="600" :step="1" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="起摊月" required>
              <el-date-picker
                v-model="createForm.start_month"
                type="month"
                value-format="YYYY-MM"
                placeholder="YYYY-MM"
                class="full-width"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="租赁到期日" required>
              <el-date-picker
                v-model="createForm.lease_end_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="YYYY-MM-DD"
                class="full-width"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="摊销到期月">
          <span class="muted">
            {{ amortizationEndMonth(createForm.start_month, createForm.months) || '按起摊月与月数自动推算' }}
            （晚于租赁到期日只作风险警告，不会自动缩短年限）
          </span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.remark" type="textarea" :rows="2" maxlength="2000" placeholder="改良范围、验收依据（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="createLoading" :disabled="!canCreate" @click="createItem">
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
  amortizationEndMonth,
  asList,
  buildItemPayload,
  deferredApi,
  emptyItemForm,
  formatMoney,
  formatRate,
  itemStatusMeta,
  runStatusMeta,
} from '../api'
import ItemPanel from './ItemPanel.vue'
import SchedulePanel from './SchedulePanel.vue'
import RiskExportPanel from './RiskExportPanel.vue'

// ---------- 项目台账 ----------
const items = ref([])
const itemsLoading = ref(false)
const summary = ref({})
const selected = ref(null)
const selectedRun = ref(null)
const activeTab = ref('params')
const refreshing = ref(false)

async function loadItems() {
  itemsLoading.value = true
  try {
    const { data } = await deferredApi.items({ page: 1, page_size: 200 })
    items.value = asList(data)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    itemsLoading.value = false
  }
}

async function loadSummary() {
  try {
    const { data } = await deferredApi.summary()
    summary.value = data || {}
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function loadAll() {
  await loadItems()
  await loadSummary()
}

async function selectItem(rowOrId) {
  const id = rowOrId && typeof rowOrId === 'object' ? rowOrId.id : rowOrId
  if (!id) return
  try {
    const { data } = await deferredApi.item(id)
    selected.value = data
    selectedRun.value = null
    activeTab.value = 'params'
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function refreshItem() {
  if (!selected.value) return
  const id = selected.value.id
  refreshing.value = true
  try {
    const { data } = await deferredApi.item(id)
    selected.value = data
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    refreshing.value = false
  }
}

function onRunChange(run) {
  selectedRun.value = run || null
}

const selectedStale = computed(() => Boolean(selected.value?.latest_run_stale))

async function archiveItem(row) {
  try {
    await ElMessageBox.confirm(
      `确认归档项目「${row.project_code}」？归档后项目参数只读、不能再发起测算，已有测算仍可查看与导出。`,
      '归档确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deferredApi.patchItem(row.id, { status: 'archived' })
    ElMessage.success('项目已归档（只读）')
    await loadAll()
    if (selected.value?.id === row.id) await selectItem(row.id)
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function removeItem(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除项目「${row.project_code}」？存在测算历史的项目后端会拒绝删除（409），请改为归档。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deferredApi.deleteItem(row.id)
    ElMessage.success('项目已删除')
    if (selected.value?.id === row.id) {
      selected.value = null
      selectedRun.value = null
    }
    await loadAll()
  } catch {
    /* 拦截器已统一提示 */
  }
}

// ---------- 新建项目 ----------
const createVisible = ref(false)
const createLoading = ref(false)
const createForm = reactive(emptyItemForm())

const canCreate = computed(
  () =>
    Boolean(String(createForm.project_code || '').trim()) &&
    Boolean(String(createForm.name || '').trim()) &&
    Boolean(String(createForm.project || '').trim()) &&
    createForm.amount_incl_tax !== null &&
    createForm.amount_incl_tax !== '' &&
    createForm.tax_rate !== null &&
    createForm.tax_rate !== '' &&
    Boolean(createForm.start_month) &&
    Number(createForm.months) >= 1 &&
    Boolean(createForm.lease_end_date),
)

function openCreate() {
  Object.assign(createForm, emptyItemForm())
  createVisible.value = true
}

async function createItem() {
  if (createLoading.value) return
  let payload
  try {
    payload = buildItemPayload(createForm)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  createLoading.value = true
  try {
    const { data } = await deferredApi.createItem(payload)
    ElMessage.success(`项目「${data.project_code || payload.project_code}」已创建（草稿）`)
    createVisible.value = false
    await loadAll()
    await selectItem(data.id)
  } catch {
    /* 拦截器已统一提示；编码重复等由后端返回中文 409/400 */
  } finally {
    createLoading.value = false
  }
}

onMounted(loadAll)

defineExpose({
  items,
  itemsLoading,
  summary,
  selected,
  selectedRun,
  activeTab,
  refreshing,
  loadItems,
  loadSummary,
  loadAll,
  selectItem,
  refreshItem,
  onRunChange,
  selectedStale,
  archiveItem,
  removeItem,
  createVisible,
  createLoading,
  createForm,
  canCreate,
  openCreate,
  createItem,
  amortizationEndMonth,
  formatMoney,
  formatRate,
  itemStatusMeta,
  runStatusMeta,
  STALE_HINT,
})
</script>

<style scoped>
.page {
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
.item-title {
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
