<template>
  <el-card shadow="never" class="block">
    <template #header>
      <div class="card-head">
        <span>
          部门明细 · {{ batch.entity }} · {{ batch.period }}
          <el-tag size="small" :type="batchStatusMeta(batch.status).type" class="ml8">
            {{ batch.status_label || batchStatusMeta(batch.status).label }}
          </el-tag>
          <el-tag size="small" type="info" class="ml8">计提率 {{ formatRate(batch.rate) }}</el-tag>
        </span>
        <span class="head-actions">
          <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
          <el-button size="small" :loading="previewing" @click="loadPreview">计算预览</el-button>
          <el-button size="small" type="primary" :disabled="readonly" :loading="saving" @click="saveLines">
            保存部门行
          </el-button>
          <el-button size="small" :disabled="!canConfirm" @click="confirmBatch">确认批次</el-button>
          <el-button size="small" type="success" :loading="exporting" @click="exportXlsx">导出 xlsx</el-button>
        </span>
      </div>
    </template>

    <el-alert type="info" :closable="false" :title="SCOPE_NOTICE" class="mb8" />
    <el-alert
      v-if="readonly"
      type="warning"
      :closable="false"
      class="mb8"
      title="该批次已确认，整体只读：部门行、费率与备注均不可修改，也不能删除。"
    />
    <el-alert
      v-else-if="blockReason"
      type="warning"
      :closable="false"
      class="mb8"
      :title="blockReason"
    />

    <el-table :data="rows" size="small" empty-text="暂无部门行：点下方「+ 加一行」录入部门与工资基数">
      <el-table-column label="部门" min-width="150">
        <template #default="{ row }">
          <el-input v-model="row.department" size="small" :disabled="readonly" placeholder="部门名称" />
        </template>
      </el-table-column>
      <el-table-column label="本期工资基数" width="150">
        <template #default="{ row }">
          <el-input-number
            v-model="row.current_wage_base"
            size="small"
            :min="0"
            :precision="2"
            :controls="false"
            :disabled="readonly"
            placeholder="留空=未录入"
            style="width: 100%"
          />
        </template>
      </el-table-column>
      <el-table-column label="上期已计提" width="150">
        <template #default="{ row }">
          <el-input-number
            v-model="row.prior_accrual"
            size="small"
            :min="0"
            :precision="2"
            :controls="false"
            :disabled="readonly"
            placeholder="留空=未录入"
            style="width: 100%"
          />
        </template>
      </el-table-column>
      <el-table-column label="上期实际数" width="150">
        <template #default="{ row }">
          <el-input-number
            v-model="row.prior_actual"
            size="small"
            :min="0"
            :precision="2"
            :controls="false"
            :disabled="readonly"
            placeholder="留空=未录入"
            style="width: 100%"
          />
        </template>
      </el-table-column>
      <el-table-column label="本期计提" width="110" align="right">
        <template #default="{ row }">{{ formatAmount(row.current_accrued) }}</template>
      </el-table-column>
      <el-table-column label="上期差异" width="110" align="right">
        <template #default="{ row }">{{ formatAmount(row.prior_variance) }}</template>
      </el-table-column>
      <el-table-column label="建议入账" width="110" align="right">
        <template #default="{ row }">{{ formatAmount(row.current_book_amount) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="lineStatusMeta(row.status || row.line_status).type">
            {{ row.status_label || row.line_status_label || lineStatusMeta(row.line_status).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ $index }">
          <el-button size="small" text type="danger" :disabled="readonly" @click="removeRow($index)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-button size="small" class="mt8" :disabled="readonly" @click="addRow">+ 加一行</el-button>

    <template v-if="preview">
      <el-divider content-position="left">计算预览（只读，不落库）</el-divider>
      <div class="totals">
        <div v-for="item in totalsRows(preview.totals)" :key="item.label" class="total-item">
          <span class="total-label">{{ item.label }}</span>
          <b>{{ item.value }}</b>
        </div>
      </div>
      <div class="hint mb8">
        规则：本期计提 = ROUND(工资基数 × 计提率, 2)；上期差异 = 上期实际 − 上期已计提（负数即冲回）；
        本期建议入账 = 本期计提 + 上期差异。合计只含可计算行，缺数据行不按 0 计入。
      </div>
      <el-table :data="preview.checks" size="small" border>
        <el-table-column prop="title" label="检查项" min-width="220" />
        <el-table-column label="结论" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="checkStateMeta(row.status).type">
              {{ checkStateMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="期望" width="110" align="right">
          <template #default="{ row }">{{ formatAmount(row.expected) }}</template>
        </el-table-column>
        <el-table-column label="实际" width="110" align="right">
          <template #default="{ row }">{{ formatAmount(row.actual) }}</template>
        </el-table-column>
        <el-table-column label="差异" width="110" align="right">
          <template #default="{ row }">{{ formatAmount(row.difference) }}</template>
        </el-table-column>
        <el-table-column prop="message" label="说明" min-width="240" />
      </el-table>
    </template>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  CONFIRM_NOTE,
  SCOPE_NOTICE,
  batchStatusMeta,
  buildLinesPayload,
  checkStateMeta,
  confirmBlockReason,
  exportFileNameFromResponse,
  formatAmount,
  formatRate,
  lineStatusMeta,
  linesToEditor,
  saveBlob,
  totalsRows,
  unionApi,
} from '../api'

const props = defineProps({
  batch: { type: Object, default: null },
})

const emit = defineEmits(['changed'])

const rows = ref([])
const preview = ref(null)
const loading = ref(false)
const saving = ref(false)
const previewing = ref(false)
const exporting = ref(false)

const readonly = computed(() => props.batch?.status === 'confirmed')
const blockReason = computed(() => (preview.value ? confirmBlockReason(preview.value) : ''))
const canConfirm = computed(
  () => !readonly.value && Boolean(preview.value) && !confirmBlockReason(preview.value),
)

function mergeComputed(editorRows, computedRows) {
  const byDepartment = new Map((computedRows || []).map((row) => [row.department, row]))
  return editorRows.map((row) => {
    const hit = byDepartment.get(row.department)
    if (!hit) return { ...row, line_status: 'unknown', line_status_label: '缺数据' }
    return {
      ...row,
      line_status: hit.line_status,
      line_status_label: hit.line_status_label,
      current_accrued: hit.current_accrued,
      prior_variance: hit.prior_variance,
      current_book_amount: hit.current_book_amount,
      note: hit.note || '',
    }
  })
}

async function loadLines() {
  if (!props.batch) return
  loading.value = true
  try {
    const { data } = await unionApi.lines(props.batch.id)
    rows.value = linesToEditor(data)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

async function loadPreview() {
  if (!props.batch) return
  previewing.value = true
  try {
    const { data } = await unionApi.preview(props.batch.id)
    preview.value = data
    rows.value = mergeComputed(rows.value, data.lines)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    previewing.value = false
  }
}

async function reload() {
  await loadLines()
  await loadPreview()
}

function addRow() {
  rows.value.push({ department: '', current_wage_base: null, prior_accrual: null, prior_actual: null })
}

function removeRow(index) {
  rows.value.splice(index, 1)
}

async function saveLines() {
  if (!props.batch) return
  let payload
  try {
    payload = buildLinesPayload(rows.value)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  saving.value = true
  try {
    const { data } = await unionApi.putLines(props.batch.id, payload)
    rows.value = linesToEditor(data)
    ElMessage.success('部门行已保存')
    await loadPreview()
    emit('changed', props.batch.id)
  } catch {
    /* 拦截器已统一提示；409（部门重复 / 已确认只读）保持当前编辑态 */
  } finally {
    saving.value = false
  }
}

async function confirmBatch() {
  if (!props.batch) return
  if (confirmBlockReason(preview.value)) {
    ElMessage.warning(confirmBlockReason(preview.value))
    return
  }
  try {
    await unionApi.confirm(props.batch.id)
    ElMessage.success('批次已确认，现为只读')
    await reload()
    emit('changed', props.batch.id)
  } catch {
    /* 拦截器已统一提示；后端 409 中文原因已说明为何不能确认 */
  }
}

async function exportXlsx() {
  if (!props.batch) return
  exporting.value = true
  try {
    const response = await unionApi.exportXlsx(props.batch.id)
    const filename = exportFileNameFromResponse(response, props.batch)
    if (!saveBlob(response.data, filename)) ElMessage.warning('浏览器不支持自动下载，请检查浏览器设置')
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    exporting.value = false
  }
}

// 挂载即拉取一次（父组件已选好批次）；之后仅在批次切换时重载
onMounted(async () => {
  if (props.batch) await reload()
})

watch(
  () => props.batch && props.batch.id,
  async (id) => {
    preview.value = null
    rows.value = []
    if (id) await reload()
  },
)

defineExpose({
  rows,
  preview,
  readonly,
  blockReason,
  canConfirm,
  loadLines,
  loadPreview,
  reload,
  addRow,
  removeRow,
  saveLines,
  confirmBatch,
  exportXlsx,
  CONFIRM_NOTE,
})
</script>

<style scoped>
.block {
  margin-bottom: 12px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.head-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.totals {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin: 6px 0 10px;
}
.total-item {
  display: flex;
  flex-direction: column;
  font-size: 13px;
  color: #303133;
}
.total-label {
  color: #909399;
  font-size: 12px;
}
.hint {
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
}
.mb8 {
  margin-bottom: 8px;
}
.mt8 {
  margin-top: 8px;
}
.ml8 {
  margin-left: 8px;
}
</style>
