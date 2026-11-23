<template>
  <div class="page">
    <HelpLink />
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>工会经费计提批次</span>
          <span class="head-actions">
            <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
            <el-button size="small" type="primary" @click="openCreate">新建批次</el-button>
          </span>
        </div>
      </template>
      <el-alert type="info" :closable="false" :title="SCOPE_NOTICE" class="mb8" />
      <div class="summary-row">
        <span class="summary-item">批次 <b>{{ summary.total_batches ?? batches.length }}</b></span>
        <span class="summary-item">草稿 <b>{{ summary.draft_count ?? 0 }}</b></span>
        <span class="summary-item">已确认 <b>{{ summary.confirmed_count ?? 0 }}</b></span>
        <span class="summary-item">缺数据行 <b class="warn">{{ summary.unknown_line_total ?? 0 }}</b></span>
        <span class="summary-item">建议入账合计 <b>{{ formatAmount(summary.current_book_amount_total) }}</b></span>
      </div>
      <el-table
        :data="batches"
        size="small"
        highlight-current-row
        empty-text="暂无计提批次：点右上角「新建批次」录入主体、所属期间与计提率"
        @current-change="onCurrentChange"
      >
        <el-table-column prop="entity" label="计提主体" min-width="150" show-overflow-tooltip />
        <el-table-column prop="period" label="所属期间" width="100" />
        <el-table-column label="计提率" width="90">
          <template #default="{ row }">{{ formatRate(row.rate) }}</template>
        </el-table-column>
        <el-table-column label="部门行" width="90">
          <template #default="{ row }">{{ row.line_count }}</template>
        </el-table-column>
        <el-table-column label="缺数据行" width="90">
          <template #default="{ row }">
            <span :class="{ warn: row.unknown_line_count > 0 }">{{ row.unknown_line_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="本期应计提" width="120" align="right">
          <template #default="{ row }">{{ formatAmount(row.current_accrued_total) }}</template>
        </el-table-column>
        <el-table-column label="上期差异" width="110" align="right">
          <template #default="{ row }">{{ formatAmount(row.prior_variance_total) }}</template>
        </el-table-column>
        <el-table-column label="建议入账" width="120" align="right">
          <template #default="{ row }">{{ formatAmount(row.current_book_amount_total) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="batchStatusMeta(row.status).type">
              {{ row.status_label || batchStatusMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" :disabled="isConfirmed(row)" @click="openEdit(row)">
              编辑
            </el-button>
            <el-button size="small" text type="danger" :disabled="isConfirmed(row)" @click="removeBatch(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <LinePanel v-if="selected" :batch="selected" @changed="reload" />
    <el-empty v-else description="请先在上方选择一个批次，再录入部门工资基数" />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑批次' : '新建计提批次'" width="520px">
      <el-form :model="form" label-width="96px" @submit.prevent>
        <el-form-item label="计提主体" required>
          <el-input v-model="form.entity" placeholder="公司/单位名称" maxlength="200" />
        </el-form-item>
        <el-form-item label="所属期间" required>
          <el-date-picker v-model="form.period" type="month" value-format="YYYY-MM" placeholder="选择期间" />
        </el-form-item>
        <el-form-item label="计提率" required>
          <el-input-number
            v-model="form.rate"
            :min="0.000001"
            :max="1"
            :step="0.005"
            :precision="6"
            :controls="false"
            placeholder="2% 填 0.02"
            style="width: 100%"
          />
          <div class="hint">按小数录入（2% 填 0.02）；必填，系统不提供默认费率</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="2000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="submitBatch">
          {{ editingId ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import HelpLink from '../../../components/HelpLink.vue'
import {
  SCOPE_NOTICE,
  asList,
  batchStatusMeta,
  batchToForm,
  buildBatchPayload,
  emptyBatchForm,
  formatAmount,
  formatRate,
  unionApi,
} from '../api'
import LinePanel from './LinePanel.vue'

const batches = ref([])
const summary = ref({})
const selected = ref(null)
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref(null)
const form = reactive(emptyBatchForm())

function isConfirmed(row) {
  return Boolean(row) && row.status === 'confirmed'
}

async function loadBatches() {
  loading.value = true
  try {
    const { data } = await unionApi.batches({ page: 1, page_size: 200 })
    batches.value = asList(data)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  try {
    const { data } = await unionApi.summary()
    summary.value = data || {}
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function reload() {
  await loadBatches()
  await loadSummary()
  if (selected.value) {
    const stillThere = batches.value.find((item) => item.id === selected.value.id)
    if (!stillThere) {
      selected.value = null
    } else {
      await selectBatch(stillThere)
    }
  }
}

async function selectBatch(rowOrId) {
  const id = rowOrId && typeof rowOrId === 'object' ? rowOrId.id : rowOrId
  if (!id) return
  try {
    const { data } = await unionApi.batch(id)
    selected.value = data.batch
  } catch {
    /* 拦截器已统一提示 */
  }
}

function onCurrentChange(row) {
  if (row) selectBatch(row)
}

function openCreate() {
  editingId.value = null
  Object.assign(form, emptyBatchForm())
  dialogVisible.value = true
}

function openEdit(row) {
  if (isConfirmed(row)) return
  editingId.value = row.id
  Object.assign(form, batchToForm(row))
  dialogVisible.value = true
}

async function submitBatch() {
  let payload
  try {
    payload = buildBatchPayload(form)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  saving.value = true
  try {
    const editing = editingId.value
    const { data } = editing
      ? await unionApi.patchBatch(editing, payload)
      : await unionApi.createBatch(payload)
    ElMessage.success(editing ? '批次已保存' : '批次已创建，请录入部门工资基数')
    dialogVisible.value = false
    editingId.value = null
    await reload()
    await selectBatch(data.id)
  } catch {
    /* 拦截器已统一提示；409（主体+期间重复）保持弹窗便于改名 */
  } finally {
    saving.value = false
  }
}

async function removeBatch(row) {
  if (isConfirmed(row)) {
    ElMessage.warning('已确认批次不允许删除')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除主体「${row.entity}」期间 ${row.period} 的批次？部门行会一并删除，且不可恢复。`,
      '删除批次',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await unionApi.deleteBatch(row.id)
    ElMessage.success('批次已删除')
    if (selected.value && selected.value.id === row.id) selected.value = null
    await reload()
  } catch {
    /* 拦截器已统一提示 */
  }
}

onMounted(async () => {
  await loadBatches()
  await loadSummary()
})

defineExpose({
  batches,
  summary,
  selected,
  form,
  dialogVisible,
  editingId,
  loading,
  saving,
  reload,
  selectBatch,
  openCreate,
  openEdit,
  submitBatch,
  removeBatch,
})
</script>

<style scoped>
.page {
  padding: 4px;
}
.block {
  margin-bottom: 12px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.summary-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin: 8px 0 10px;
  color: #606266;
  font-size: 13px;
}
.summary-item b {
  color: #303133;
}
.warn {
  color: #e6a23c;
  font-weight: 600;
}
.hint {
  color: #909399;
  font-size: 12px;
  line-height: 1.4;
}
.mb8 {
  margin-bottom: 8px;
}
</style>
