<template>
  <div class="panel">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>数据集清单</span>
          <span class="head-actions">
            <TemplateLinks type="vault" />
            <el-button size="small" type="primary" @click="uploadDialog = true">上传入池</el-button>
          </span>
        </div>
      </template>
      <el-table :data="items" size="small" empty-text="暂无数据集，点右上角上传 xlsx/csv">
        <el-table-column prop="table" label="表名" min-width="140" />
        <el-table-column prop="source" label="来源" min-width="160" show-overflow-tooltip />
        <el-table-column prop="rows" label="行数" width="90" align="right" />
        <el-table-column label="列数" width="80" align="right">
          <template #default="{ row }">{{ (row.columns || []).length }}</template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="170" />
        <el-table-column label="操作" width="420" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" :loading="previewing" @click="preview(row)">预览</el-button>
            <el-button size="small" link type="primary" @click="openProfile(row)">画像</el-button>
            <el-button size="small" link type="primary" @click="openClean(row)">清洗</el-button>
            <el-button size="small" link type="primary" @click="analyze(row.table)">分析</el-button>
            <el-button size="small" link type="primary" @click="useInQuery(row.table)">查询</el-button>
            <el-button size="small" link type="primary" @click="openCleanLog(row)">配方历史</el-button>
            <el-button size="small" link type="primary" :loading="busyWith('rename', row.table)" :disabled="!!datasetBusy" @click="renameDataset(row)">重命名</el-button>
            <el-button size="small" link type="danger" :loading="busyWith('delete', row.table)" :disabled="!!datasetBusy" @click="deleteDataset(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="uploadDialog" title="上传文件入池" width="480px">
      <el-form label-width="80px">
        <el-form-item label="表名">
          <el-input v-model="form.table" placeholder="小写字母/数字/下划线，如 t_expense" />
        </el-form-item>
        <el-form-item label="模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="append">追加</el-radio>
            <el-radio value="replace">整表重建</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="文件">
          <input type="file" accept=".xlsx,.csv" @change="onFile" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!canSubmit" @click="submitUpload">入池</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="previewDialog" :title="`预览：${previewData.table}`" width="760px">
      <el-table :data="previewRows" size="small" max-height="420" border>
        <el-table-column v-for="(c, i) in previewData.columns" :key="'c' + i" :prop="'c' + i" :label="c" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-dialog>

    <el-dialog v-model="profileDialog" :title="`列画像：${profileData.table}`" width="860px">
      <el-alert v-for="(w, i) in profileData.warnings" :key="'w' + i" :title="w" type="warning" :closable="false" class="warn-line" />
      <el-table :data="profileData.columns" size="small" max-height="420" border>
        <el-table-column prop="name" label="列名" min-width="120" />
        <el-table-column prop="duckdb_type" label="类型" width="110" />
        <el-table-column label="缺失率" width="90" align="right">
          <template #default="{ row }">{{ row.null_pct }}%</template>
        </el-table-column>
        <el-table-column label="唯一率" width="90" align="right">
          <template #default="{ row }">{{ row.distinct_pct == null ? '—' : (row.distinct_pct * 100).toFixed(1) + '%' }}</template>
        </el-table-column>
        <el-table-column label="高频值" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="(tv, i) in row.top_values" :key="i" size="small" class="tv-tag">{{ tv.value }}×{{ tv.count }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="cleanDialog" :title="`清洗：${cleanSource}`" width="880px">
      <el-form label-width="120px" size="small">
        <el-form-item label="脏值归一">
          <el-switch v-model="recipe.normalize_dirty_text" />
          <span class="opt-hint">「— / 无 / #DIV/0!」等占位值转空值，千分位/全角/百分号数值归一</span>
        </el-form-item>
        <el-form-item label="剔除整行全空">
          <el-switch v-model="recipe.drop_all_empty_rows" />
        </el-form-item>
        <el-form-item label="剔除全空列">
          <el-switch v-model="recipe.drop_all_empty_columns" />
        </el-form-item>
        <el-form-item label="整行去重">
          <el-switch v-model="recipe.dedupe_rows" />
        </el-form-item>
        <el-form-item label="关键列必填">
          <el-select v-model="recipe.required_columns" multiple placeholder="选择必须非空的列" clearable style="width: 100%">
            <el-option v-for="c in cleanColumns" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="无效值处理">
          <el-radio-group v-model="recipe.invalid_policy">
            <el-radio value="drop_row">剔除该行</el-radio>
            <el-radio value="null_cell">置空该格</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="IQR 离群标记">
          <el-switch v-model="recipe.mark_outliers_iqr" />
          <span class="opt-hint">只标记进报告，不剔除行</span>
        </el-form-item>
        <el-form-item label="结果表名">
          <el-input v-model="cleanResultTable" style="width: 280px" placeholder="默认 {源表}_clean" />
          <span class="opt-hint">结果落新数据集，源数据不动</span>
        </el-form-item>
        <el-form-item label="同名结果表">
          <el-checkbox v-model="cleanAllowOverwrite">允许覆盖已存在的结果表</el-checkbox>
          <span class="opt-hint">不勾选时后端拒绝覆盖（409），避免误伤已有数据集</span>
        </el-form-item>
      </el-form>
      <el-alert v-if="cleanSameAsSource" title="结果表名不能与源表相同：清洗结果不能覆盖源数据，请换一个名字。" type="error" :closable="false" class="warn-line" />
      <el-alert v-if="cleanConflictHint" :title="cleanConflictHint" type="warning" :closable="false" class="warn-line" />
      <template #footer>
        <el-button @click="openCleanLog()">历史配方</el-button>
        <el-button @click="runCleanPreview" :loading="cleanPreviewing" :disabled="!cleanResultTableValid">预览清洗</el-button>
        <el-button type="primary" :loading="cleanApplying" :disabled="!cleanReport || !cleanResultTableValid" @click="applyClean">应用清洗</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="cleanReportDialog" :title="`清洗报告：${cleanSource}`" width="880px">
      <el-descriptions :column="3" size="small" border>
        <el-descriptions-item label="入池行数">{{ cleanReport?.rows_in }}</el-descriptions-item>
        <el-descriptions-item label="保留行数">{{ cleanReport?.rows_kept }}</el-descriptions-item>
        <el-descriptions-item label="结果表">{{ cleanReport?.result_table || cleanReport?.target_table_default || '—' }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="cleanReport?.ops || []" size="small" class="gap-top" border>
        <el-table-column prop="op" label="算子" width="200" />
        <el-table-column label="命中" width="120" align="right">
          <template #default="{ row }">{{ row.hits }} {{ row.unit === 'cells' ? '格' : row.unit === 'cols' ? '列' : '行' }}</template>
        </el-table-column>
      </el-table>
      <p v-if="cleanReport?.warnings?.length" class="gap-top warn-text">{{ cleanReport.warnings.join('；') }}</p>
      <p v-if="cleanReport?.samples?.length" class="gap-top">被剔样本（最多 10 行）：</p>
      <el-table v-if="cleanReport?.samples?.length" :data="cleanReport.samples" size="small" max-height="240" border>
        <el-table-column prop="reason" label="原因" width="130" />
        <el-table-column label="内容" min-width="240">
          <template #default="{ row }">{{ sampleText(row.values) }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="cleanLogDialog" title="历史清洗配方" width="760px">
      <el-table :data="cleanLogs" size="small" max-height="420" border>
        <el-table-column prop="created_at" label="时间" width="160" />
        <el-table-column prop="source_table" label="源表" width="120" />
        <el-table-column prop="result_table" label="结果表" width="120" />
        <el-table-column label="操作">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="reuseRecipe(row)">套用此配方</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="!cleanLogs.length" class="empty-hint">暂无清洗历史</p>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../../api'
import TemplateLinks from '../../../../components/TemplateLinks.vue'
import { takeFile } from '../../../_shared/fileInput'
import { toTableRows } from '../../tableRows'
import {
  buildCleanPayload,
  datasetUsageUrl,
  defaultResultTable,
  deleteConfirmText,
  deleteUrl,
  describeCleanConflict,
  forceDeleteConfirmText,
  normalizeUsage,
  renameConfirmText,
  renameRequestBody,
  renameUrl,
  sameDataset,
  validateTableName,
} from '../../poolLogic'

const emit = defineEmits(['jump-to-analyze'])
const router = useRouter()

const loading = ref(false)
const items = ref([])

// ---------- 上传入池（迁移自原 TableListView） ----------
const uploadDialog = ref(false)
const submitting = ref(false)
// 默认追加：整表重建会清空仓里已有的同名表，必须是用户的显式选择
const form = reactive({ table: '', mode: 'append', file: null })
const canSubmit = computed(() => form.table.trim() && form.file)

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/vault/tables')
    items.value = data.items || []
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function onFile(e) {
  form.file = takeFile(e)
}

async function submitUpload() {
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  try {
    if (form.mode === 'replace' && items.value.some((t) => t.table === form.table.trim())) {
      await ElMessageBox.confirm(
        `表「${form.table.trim()}」已存在，整表重建将覆盖现有数据，确定继续吗？`,
        '覆盖确认',
        { type: 'warning' },
      )
    }
    const fd = new FormData()
    fd.append('file', form.file)
    const { data } = await api.post(
      `/finance/vault/ingest/file?table=${encodeURIComponent(form.table.trim())}&mode=${form.mode}`,
      fd,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 },
    )
    ElMessage.success(`入池成功：${data.rows} 行`)
    uploadDialog.value = false
    form.table = ''
    form.file = null
    form.mode = 'append'
    await load()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    submitting.value = false
  }
}

// ---------- 数据预览（迁移自原 TableListView） ----------
const previewDialog = ref(false)
const previewData = reactive({ table: '', columns: [], rows: [] })
const previewing = ref(false)
let previewSeq = 0
const previewRows = computed(() => toTableRows(previewData.columns, previewData.rows))

async function preview(row) {
  const seq = ++previewSeq
  previewing.value = true
  try {
    const { data } = await api.get(`/finance/vault/tables/${encodeURIComponent(row.table)}/preview`)
    if (seq !== previewSeq) return
    previewData.table = data.table
    previewData.columns = data.columns
    previewData.rows = data.rows
    previewDialog.value = true
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    if (seq === previewSeq) previewing.value = false
  }
}

function useInQuery(table) {
  router.push({ path: '/finance/vault/query', query: { tab: 'sql', table } })
}

function analyze(table) {
  emit('jump-to-analyze', table)
}

onMounted(load)

// ---------- 列画像 ----------
const profileDialog = ref(false)
const profileData = reactive({ table: '', columns: [], warnings: [] })

async function openProfile(row) {
  try {
    const { data } = await api.get(`/finance/vault/tables/${encodeURIComponent(row.table)}/profile`)
    profileData.table = data.table
    profileData.columns = data.columns || []
    profileData.warnings = data.warnings || []
    profileDialog.value = true
  } catch {
    /* 拦截器已统一提示 */
  }
}

// ---------- 清洗（配方 + 预览 + 应用 + 历史配方） ----------
const cleanDialog = ref(false)
const cleanReportDialog = ref(false)
const cleanSource = ref('')
const cleanColumns = ref([])
const cleanReport = ref(null)
const cleanResultTable = ref('')
const cleanAllowOverwrite = ref(false)
const cleanConflictHint = ref('')
const cleanPreviewing = ref(false)
const cleanApplying = ref(false)
const recipe = reactive({
  normalize_dirty_text: true,
  drop_all_empty_rows: true,
  drop_all_empty_columns: true,
  dedupe_rows: true,
  required_columns: [],
  invalid_policy: 'drop_row',
  mark_outliers_iqr: false,
})

const cleanSameAsSource = computed(() => sameDataset(cleanResultTable.value, cleanSource.value))
// 目标与源表同名会替换源数据集本身：请求前就拒绝，绝不引导用户勾选覆盖
const cleanResultTableValid = computed(() => validateTableName(cleanResultTable.value) && !cleanSameAsSource.value)

// 换源表/复用配方时统一复位清洗上下文：报告、结果表名、覆盖勾选与冲突提示不跨表遗留
function resetCleanContext(table) {
  cleanSource.value = table
  cleanReport.value = null
  cleanReportDialog.value = false
  cleanResultTable.value = defaultResultTable(table)
  cleanAllowOverwrite.value = false
  cleanConflictHint.value = ''
}

function openClean(row) {
  resetCleanContext(row.table)
  cleanColumns.value = (row.columns || []).map((c) => c.name)
  recipe.required_columns = []
  cleanDialog.value = true
}

function cleanPayload() {
  return buildCleanPayload(recipe, {
    resultTable: cleanResultTable.value,
    allowOverwrite: cleanAllowOverwrite.value,
  })
}

async function runCleanPreview() {
  if (!cleanResultTableValid.value) return
  cleanPreviewing.value = true
  cleanConflictHint.value = ''
  try {
    const { data } = await api.post(
      `/finance/vault/tables/${encodeURIComponent(cleanSource.value)}/clean/preview`,
      cleanPayload(),
    )
    cleanReport.value = data
    // 结果表名必须显式填写（空名 / 与源表同名都会被 cleanResultTableValid 挡在请求前），
    // 报告展示「将要写入」的结果表名，而不是后端默认名
    cleanReport.value.result_table = cleanResultTable.value
    cleanReportDialog.value = true
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    cleanPreviewing.value = false
  }
}

async function applyClean() {
  if (!cleanReport.value || !cleanResultTableValid.value) return
  cleanApplying.value = true
  cleanConflictHint.value = ''
  try {
    const { data } = await api.post(
      `/finance/vault/tables/${encodeURIComponent(cleanSource.value)}/clean/apply`,
      cleanPayload(),
    )
    cleanReport.value = data.report || cleanReport.value
    cleanReport.value.result_table = data.result_table
    ElMessage.success(`清洗完成：${data.rows} 行 → ${data.result_table}`)
    cleanDialog.value = false
    cleanReportDialog.value = true
    await load()
  } catch (err) {
    // 结果表已存在（409）只改弹窗内的可操作提示，不重复弹全局 Toast
    cleanConflictHint.value = describeCleanConflict(err, cleanResultTable.value)
  } finally {
    cleanApplying.value = false
  }
}

function sampleText(values) {
  return Object.entries(values || {})
    .map(([k, v]) => `${k}=${v}`)
    .join(' | ')
}

// ---------- 历史配方 ----------
const cleanLogDialog = ref(false)
const cleanLogs = ref([])

async function openCleanLog(row) {
  // 从表行进入配方历史时先同步清洗上下文，「套用此配方」才能回到正确的源表
  if (row && row.table) {
    resetCleanContext(row.table)
    cleanColumns.value = (row.columns || []).map((c) => c.name)
  }
  cleanLogDialog.value = true
  const table = row ? row.table : cleanSource.value
  try {
    const { data } = await api.get(`/finance/vault/tables/${encodeURIComponent(table)}/clean/log`)
    cleanLogs.value = data.items || []
  } catch {
    cleanLogs.value = []
  }
}

// 套用历史配方：把日志里的配方填回清洗弹窗，并回填该次结果表名（同名冲突需显式勾选覆盖）
function reuseRecipe(row) {
  const ops = row.recipe?.ops || {}
  recipe.normalize_dirty_text = ops.normalize_dirty_text !== false
  recipe.drop_all_empty_rows = ops.drop_all_empty_rows !== false
  recipe.drop_all_empty_columns = ops.drop_all_empty_columns !== false
  recipe.dedupe_rows = ops.dedupe_rows !== false
  recipe.required_columns = Array.isArray(ops.required_columns) ? ops.required_columns : []
  recipe.invalid_policy = row.recipe?.invalid_policy || 'drop_row'
  recipe.mark_outliers_iqr = !!row.recipe?.mark_outliers_iqr
  cleanResultTable.value = row.result_table || defaultResultTable(cleanSource.value)
  cleanAllowOverwrite.value = false
  cleanConflictHint.value = ''
  cleanReport.value = null
  cleanReportDialog.value = false
  cleanLogDialog.value = false
  cleanDialog.value = true
}

// ---------- 数据集删除 / 重命名（传错数据、二次清洗后误留的数据集可恢复） ----------

// 删除/重命名共用一个在途标记：双击只允许一个动作（第二次会拿到 404/409 并弹错）
const datasetBusy = ref('')

function busyWith(kind, table) {
  return datasetBusy.value === `${kind}:${table}`
}

// 预检失败（数据集已消失/接口不可用）返回 null：不盲删，错误由拦截器统一提示
async function loadDatasetUsage(table) {
  try {
    const { data } = await api.get(datasetUsageUrl(table))
    return normalizeUsage(data)
  } catch {
    return null
  }
}

async function deleteDataset(row) {
  if (datasetBusy.value) return
  datasetBusy.value = `delete:${row.table}`
  try {
    const usage = await loadDatasetUsage(row.table)
    if (!usage) return
    await ElMessageBox.confirm(
      deleteConfirmText(row.table, { cleanLogs: usage.cleanLogs, issues: usage.issues }),
      '删除数据集',
      { type: 'warning', confirmButtonText: '继续', cancelButtonText: '取消' },
    )
    let force = false
    if (usage.requiresForce) {
      // 被清洗日志引用或有未登记残留：删除会让历史配方失去来源，必须再确认一次
      await ElMessageBox.confirm(forceDeleteConfirmText(row.table, usage.cleanLogs), '二次确认：强制删除', {
        type: 'error',
        confirmButtonText: '强制删除',
        cancelButtonText: '取消',
      })
      force = true
    }
    const { data } = await api.delete(deleteUrl(row.table, row.table, force))
    ElMessage.success(`已删除数据集：${data.table || row.table}`)
    await load()
  } catch {
    /* 用户取消，或后端拒绝（拦截器已统一提示） */
  } finally {
    datasetBusy.value = ''
  }
}

async function renameDataset(row) {
  if (datasetBusy.value) return
  datasetBusy.value = `rename:${row.table}`
  try {
    const { value } = await ElMessageBox.prompt(
      '新表名（小写字母/数字/下划线，最长 64 位）',
      `重命名：${row.table}`,
      {
        inputValue: row.table,
        inputPattern: /^[a-z0-9_]{1,64}$/,
        inputErrorMessage: '仅允许小写字母/数字/下划线，最长 64 位',
      },
    )
    const next = String(value || '').trim()
    if (!validateTableName(next)) {
      ElMessage.warning('表名仅允许小写字母/数字/下划线，最长 64 位')
      return
    }
    if (next === row.table) return
    await ElMessageBox.confirm(renameConfirmText(row.table, next), '重命名确认', { type: 'warning' })
    await api.post(renameUrl(row.table), renameRequestBody(next))
    ElMessage.success(`已重命名为：${next}`)
    await load()
  } catch {
    /* 用户取消，或目标已存在/视图异常（后端 409，拦截器已统一提示） */
  } finally {
    datasetBusy.value = ''
  }
}
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.opt-hint {
  margin-left: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.warn-line {
  margin-bottom: 8px;
}
.gap-top {
  margin-top: 10px;
}
.warn-text {
  color: var(--el-color-warning);
  font-size: 12px;
}
.tv-tag {
  margin: 2px 4px 2px 0;
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>


