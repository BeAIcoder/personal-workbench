<template>
  <div class="panel">
    <el-alert
      v-if="batch && !canEdit"
      :title="batch.ever_ready
        ? '曾就绪批次的资料为审计留痕：源文件与合同范围不可再修改，口径参数仍可调整后重跑。'
        : `批次当前状态「${statusLabel}」：仅草稿/失败批次可上传源文件、删除源文件或修改合同范围。`"
      type="info"
      :closable="false"
      class="gap-bottom"
    />
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>源文件（核销 / 认领 / 欠费 / 账单）</span>
          <span class="head-actions">
            <el-button size="small" :loading="sourcesLoading" @click="loadSources">刷新</el-button>
            <el-button size="small" type="primary" :disabled="!canEdit" @click="openUpload('writeoff')">上传源文件</el-button>
          </span>
        </div>
      </template>
      <el-table :data="sources" size="small" empty-text="尚未上传源文件；勾稽检查要求核销/认领/欠费/账单四类各至少一份">
        <el-table-column label="类别" width="90">
          <template #default="{ row }">{{ row.kind_label || row.kind }}</template>
        </el-table-column>
        <el-table-column prop="original_name" label="原文件名" min-width="200" show-overflow-tooltip />
        <el-table-column label="解析状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="parseStatusMeta(row.parse_status).type">{{ parseStatusMeta(row.parse_status).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="row_count" label="行数" width="80" align="right" />
        <el-table-column label="检测到的合同" min-width="180">
          <template #default="{ row }">
            <template v-if="row.contracts && row.contracts.length">
              <el-tag v-for="c in row.contracts" :key="c" size="small" class="ctag">{{ c }}</el-tag>
            </template>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="失败原因" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.error || '—' }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="上传时间" width="160" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="danger" :disabled="!canEdit" @click="deleteSourceRow(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>合同范围勾选（check 前必须显式勾选，未勾选的合同仅可查看、不参与汇总）</span>
          <el-button size="small" type="primary" :disabled="!canEdit" :loading="scopeSaving" @click="saveScope">保存范围</el-button>
        </div>
      </template>
      <el-checkbox-group v-model="scopeChecked" class="scope-group" :disabled="!canEdit">
        <el-checkbox v-for="c in detectedContracts" :key="c" :value="c">{{ c }}</el-checkbox>
      </el-checkbox-group>
      <p v-if="!detectedContracts.length" class="empty-hint">尚未检测到任何合同：先上传源文件，这里会列出文件中出现的合同号。</p>
      <p class="muted">范围保存到批次（PATCH）；空范围不允许执行勾稽检查。</p>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>口径参数（逐合同；缺参的检查保持「待定」，绝不取默认值）</span>
          <el-button size="small" :loading="termsLoading" @click="loadTerms">刷新</el-button>
        </div>
      </template>
      <el-table :data="terms" size="small" empty-text="暂无合同口径参数；合同范围保存后这里会逐合同列出">
        <el-table-column prop="contract_no" label="合同号" min-width="160" />
        <el-table-column label="完整性" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.complete ? 'success' : 'warning'">{{ row.complete ? '已录全' : '缺参' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="缺失项" min-width="220">
          <template #default="{ row }">{{ missingText(row) }}</template>
        </el-table-column>
        <el-table-column label="计费方式" width="120">
          <template #default="{ row }">{{ row.terms ? (billingLabel(row.terms.billing_method) || '未指定') : '未录入' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openTerms(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="uploadDialog" title="上传源文件" width="520px">
      <el-form label-width="90px">
        <el-form-item label="文件类别">
          <el-radio-group v-model="uploadKind">
            <el-radio v-for="k in SOURCE_KINDS" :key="k" :value="k">{{ KIND_LABELS[k] }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="文件">
          <el-upload
            v-model:file-list="uploadList"
            drag
            action="#"
            :auto-upload="false"
            :limit="1"
            accept=".xlsx,.xls,.pdf"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
          >
            <div class="upload-text">拖拽文件到此处，或 <em>点击选择</em></div>
            <template #tip>
              <div class="el-upload__tip">核销/认领/欠费为 xlsx，账单为 PDF；同一批次内容重复的文件会自动跳过（skipped_duplicates）。</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadFile" @click="submitUpload">上传</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="termsDialog" :title="`口径参数：${termsContract}`" width="860px" top="6vh">
      <el-form label-width="150px" size="small" class="terms-form">
        <el-form-item label="重构截止口径（必填）">
          <el-radio-group v-model="editor.reconstruction_cutoff">
            <el-radio v-for="o in RECONSTRUCTION_CUTOFF_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="计费方式">
          <el-radio-group v-model="editor.billing_method">
            <el-radio v-for="o in BILLING_METHOD_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
            <el-radio value="">暂不指定（相关检查保持待定）</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="editor.billing_method === 'whole_month' || editor.billing_method === 'actual_days'">
          <el-form-item label="合同起止">
            <el-date-picker v-model="editor.contract_start" type="date" value-format="YYYY-MM-DD" placeholder="起始日" class="date-inline" />
            <el-date-picker v-model="editor.contract_end" type="date" value-format="YYYY-MM-DD" placeholder="截止日" class="date-inline" />
          </el-form-item>
          <el-form-item label="月费标准">
            <el-table :data="editor.monthly_fees" size="small" class="inline-table" empty-text="无月费标准行">
              <el-table-column label="费用类型" min-width="180">
                <template #default="{ row }"><el-input v-model="row.fee_type" placeholder="如 固定租金" /></template>
              </el-table-column>
              <el-table-column label="月金额" width="180">
                <template #default="{ row }"><el-input-number v-model="row.monthly_amount" :precision="2" :min="0" :controls="false" class="num-input" /></template>
              </el-table-column>
              <el-table-column label="操作" width="70">
                <template #default="{ $index }"><el-button size="small" link type="danger" @click="editor.monthly_fees.splice($index, 1)">删除</el-button></template>
              </el-table-column>
            </el-table>
            <el-button size="small" class="gap-top-sm" @click="editor.monthly_fees.push({ fee_type: '', monthly_amount: null })">加一行</el-button>
          </el-form-item>
        </template>
        <template v-if="editor.billing_method === 'custom_lines'">
          <el-form-item label="自定义计费明细">
            <el-table :data="editor.custom_lines" size="small" class="inline-table" empty-text="无明细行">
              <el-table-column label="费用类型" min-width="140">
                <template #default="{ row }"><el-input v-model="row.fee_type" /></template>
              </el-table-column>
              <el-table-column label="费用期起" width="150">
                <template #default="{ row }"><el-date-picker v-model="row.period_start" type="date" value-format="YYYY-MM-DD" /></template>
              </el-table-column>
              <el-table-column label="费用期止" width="150">
                <template #default="{ row }"><el-date-picker v-model="row.period_end" type="date" value-format="YYYY-MM-DD" /></template>
              </el-table-column>
              <el-table-column label="金额" width="160">
                <template #default="{ row }"><el-input-number v-model="row.amount" :precision="2" :min="0" :controls="false" class="num-input" /></template>
              </el-table-column>
              <el-table-column label="操作" width="70">
                <template #default="{ $index }"><el-button size="small" link type="danger" @click="editor.custom_lines.splice($index, 1)">删除</el-button></template>
              </el-table-column>
            </el-table>
            <el-button size="small" class="gap-top-sm" @click="editor.custom_lines.push({ fee_type: '', period_start: null, period_end: null, amount: null })">加一行</el-button>
          </el-form-item>
        </template>
        <el-form-item label="一次性费用">
          <div class="full-width">
            <el-table :data="editor.additional_charges" size="small" class="inline-table" empty-text="无（保证金/燃气等一次性费用，如无则不填）">
              <el-table-column label="费用类型" min-width="130">
                <template #default="{ row }"><el-input v-model="row.fee_type" /></template>
              </el-table-column>
              <el-table-column label="金额" width="150">
                <template #default="{ row }"><el-input-number v-model="row.amount" :precision="2" :min="0" :controls="false" class="num-input" /></template>
              </el-table-column>
              <el-table-column label="期间起（可选）" width="150">
                <template #default="{ row }"><el-date-picker v-model="row.period_start" type="date" value-format="YYYY-MM-DD" /></template>
              </el-table-column>
              <el-table-column label="期间止（可选）" width="150">
                <template #default="{ row }"><el-date-picker v-model="row.period_end" type="date" value-format="YYYY-MM-DD" /></template>
              </el-table-column>
              <el-table-column label="备注" min-width="130">
                <template #default="{ row }"><el-input v-model="row.note" /></template>
              </el-table-column>
              <el-table-column label="操作" width="70">
                <template #default="{ $index }"><el-button size="small" link type="danger" @click="editor.additional_charges.splice($index, 1)">删除</el-button></template>
              </el-table-column>
            </el-table>
            <el-button size="small" class="gap-top-sm" @click="editor.additional_charges.push({ fee_type: '', amount: null, period_start: null, period_end: null, note: '' })">加一行</el-button>
          </div>
        </el-form-item>
        <el-form-item label="已确认抵扣项">
          <div class="full-width">
            <el-table :data="editor.credits" size="small" class="inline-table" empty-text="无（只有勾选「已确认」的抵扣才进入结算草稿）">
              <el-table-column label="抵扣类型" min-width="130">
                <template #default="{ row }"><el-input v-model="row.credit_type" /></template>
              </el-table-column>
              <el-table-column label="金额" width="150">
                <template #default="{ row }"><el-input-number v-model="row.amount" :precision="2" :min="0" :controls="false" class="num-input" /></template>
              </el-table-column>
              <el-table-column label="备注" min-width="130">
                <template #default="{ row }"><el-input v-model="row.note" /></template>
              </el-table-column>
              <el-table-column label="已确认" width="90">
                <template #default="{ row }"><el-switch v-model="row.confirmed" /></template>
              </el-table-column>
              <el-table-column label="操作" width="70">
                <template #default="{ $index }"><el-button size="small" link type="danger" @click="editor.credits.splice($index, 1)">删除</el-button></template>
              </el-table-column>
            </el-table>
            <el-button size="small" class="gap-top-sm" @click="editor.credits.push({ credit_type: '', amount: null, note: '', confirmed: false })">加一行</el-button>
          </div>
        </el-form-item>
        <el-form-item label="缴费到期规则">
          <el-select v-model="editor.due_rule" clearable placeholder="不指定（违约金无法测算则保持待定）" class="rule-select">
            <el-option v-for="o in DUE_RULE_OPTIONS" :key="o.value" :value="o.value" :label="o.label" />
          </el-select>
          <el-input-number
            v-if="editor.due_rule === 'expense_month_day'"
            v-model="editor.due_day"
            :min="1"
            :max="31"
            :controls="false"
            placeholder="日"
            class="num-input day-input"
          />
        </el-form-item>
        <el-form-item label="违约金（参考测算）">
          <div class="full-width">
            <span class="inline-label">日费率</span>
            <el-input-number v-model="editor.penalty.rate_per_day" :precision="8" :min="0" :max="9999.99999999" :controls="false" class="num-input" />
            <span class="inline-label">起算规则</span>
            <el-select v-model="editor.penalty.start_rule" clearable placeholder="不指定" class="rule-select">
              <el-option v-for="o in PENALTY_START_RULE_OPTIONS" :key="o.value" :value="o.value" :label="o.label" />
            </el-select>
            <span class="inline-label">封顶</span>
            <el-input-number v-model="editor.penalty.cap" :precision="2" :min="0" :controls="false" class="num-input" />
            <el-checkbox v-model="editor.penalty.waived">已豁免（仅记录，不再测算）</el-checkbox>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="termsDialog = false">取消</el-button>
        <el-button type="primary" :loading="termsSaving" @click="saveTerms">保存口径参数</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  BILLING_METHOD_OPTIONS,
  DUE_RULE_OPTIONS,
  KIND_LABELS,
  PENALTY_START_RULE_OPTIONS,
  RECONSTRUCTION_CUTOFF_OPTIONS,
  SOURCE_KINDS,
  buildTermsPayload,
  emptyContractTerms,
  parseStatusMeta,
  reconApi,
  termsToEditor,
} from '../api'

const props = defineProps({ batch: { type: Object, default: null } })
const emit = defineEmits(['refresh'])

const statusLabel = computed(() => props.batch?.status_label || props.batch?.status || '')
const canEdit = computed(
  () => !!props.batch && !props.batch.ever_ready && ['draft', 'failed'].includes(props.batch.status),
)

// ---------- 源文件列表 ----------
const sources = ref([])
const sourcesLoading = ref(false)

async function loadSources() {
  if (!props.batch) {
    sources.value = []
    return
  }
  sourcesLoading.value = true
  try {
    const { data } = await reconApi.sources(props.batch.id)
    sources.value = data.items || []
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    sourcesLoading.value = false
  }
  syncScopeFromBatch()
}

function syncScopeFromBatch() {
  scopeChecked.value = [...(props.batch?.contract_scope || [])]
}

// 检测合同 = 各源文件解析出的合同号并集 + 批次已勾选范围（保序去重）
const detectedContracts = computed(() => {
  const out = []
  for (const c of props.batch?.contract_scope || []) {
    if (c && !out.includes(c)) out.push(c)
  }
  for (const source of sources.value) {
    for (const c of source.contracts || []) {
      if (c && !out.includes(c)) out.push(c)
    }
  }
  return out
})

const scopeChecked = ref([])
const scopeSaving = ref(false)

async function saveScope() {
  if (!props.batch || !canEdit.value) return
  scopeSaving.value = true
  try {
    const scope = scopeChecked.value.map((item) => String(item || '').trim()).filter(Boolean)
    await reconApi.patchBatch(props.batch.id, { contract_scope: scope })
    scopeChecked.value = scope
    ElMessage.success('合同范围已保存')
    emit('refresh')
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    scopeSaving.value = false
  }
}

// ---------- 上传（el-upload limit=1：换文件先经 on-remove 复位） ----------
const uploadDialog = ref(false)
const uploadKind = ref('writeoff')
const uploadFile = ref(null)
const uploadList = ref([])
const uploading = ref(false)

function openUpload(kind) {
  uploadKind.value = kind
  uploadFile.value = null
  uploadList.value = []
  uploadDialog.value = true
}

function onFileChange(uploadFileArg) {
  uploadFile.value = uploadFileArg?.raw || null
}

function onFileRemove() {
  uploadFile.value = null
}

async function submitUpload() {
  if (!props.batch || !uploadFile.value || uploading.value) return
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', uploadFile.value)
    await reconApi.uploadSource(props.batch.id, uploadKind.value, fd)
    ElMessage.success(`「${KIND_LABELS[uploadKind.value]}」源文件上传成功`)
    uploadDialog.value = false
    uploadFile.value = null
    uploadList.value = []
    await loadSources()
    emit('refresh')
  } catch {
    /* 拦截器已统一提示；弹窗保留已选类别，便于修正后重试 */
  } finally {
    uploading.value = false
  }
}

async function deleteSourceRow(row) {
  if (!props.batch || !canEdit.value) return
  try {
    await ElMessageBox.confirm(
      `删除源文件「${row.original_name}」将同时删除其导入的 ${row.row_count} 行记录，确定删除吗？`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await reconApi.deleteSource(props.batch.id, row.id)
    ElMessage.success('源文件已删除')
    await loadSources()
    emit('refresh')
  } catch {
    /* 拦截器已统一提示（含人工引用 409 守卫） */
  }
}

// ---------- 口径参数（固定 schema） ----------
const terms = ref([])
const termsLoading = ref(false)
const termsDialog = ref(false)
const termsContract = ref('')
const termsSaving = ref(false)
const editor = reactive(emptyContractTerms())

function billingLabel(value) {
  return BILLING_METHOD_OPTIONS.find((o) => o.value === value)?.label || ''
}

async function loadTerms() {
  if (!props.batch) {
    terms.value = []
    return
  }
  termsLoading.value = true
  try {
    const { data } = await reconApi.terms(props.batch.id)
    terms.value = data.items || []
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    termsLoading.value = false
  }
}

function missingText(item) {
  return (item?.missing || []).join('、') || '—'
}

function openTerms(item) {
  termsContract.value = item.contract_no
  Object.assign(editor, termsToEditor(item.terms))
  termsDialog.value = true
}

async function saveTerms() {
  if (!props.batch || !termsContract.value) return
  let payload
  try {
    payload = buildTermsPayload(editor)
  } catch (exc) {
    ElMessage.warning(exc?.message || '口径参数未填写完整')
    return
  }
  termsSaving.value = true
  try {
    await reconApi.putTerms(props.batch.id, termsContract.value, payload)
    ElMessage.success(
      props.batch.ever_ready || props.batch.status === 'ready'
        ? '口径参数已保存；需重新执行勾稽检查后再导出报告'
        : '口径参数已保存',
    )
    termsDialog.value = false
    await loadTerms()
    emit('refresh')
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    termsSaving.value = false
  }
}

// 首次挂载即拉取源列表与口径参数（页签 lazy，按 batch.id key 每次只挂一次；加载函数自带守卫与 catch）
onMounted(() => {
  void loadSources()
  void loadTerms()
})

defineExpose({
  sources,
  sourcesLoading,
  loadSources,
  detectedContracts,
  scopeChecked,
  saveScope,
  canEdit,
  uploadDialog,
  uploadKind,
  uploadFile,
  uploadList,
  uploading,
  openUpload,
  onFileChange,
  onFileRemove,
  submitUpload,
  deleteSourceRow,
  terms,
  termsLoading,
  loadTerms,
  missingText,
  termsDialog,
  termsContract,
  editor,
  openTerms,
  saveTerms,
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
}
.head-actions {
  display: flex;
  gap: 8px;
}
.ctag {
  margin-right: 6px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 6px 0 0;
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0 0 6px;
}
.gap-bottom {
  margin-bottom: 12px;
}
.gap-top-sm {
  margin-top: 6px;
}
.full-width {
  width: 100%;
}
.inline-table {
  width: 100%;
}
.num-input {
  width: 130px;
}
.date-inline {
  width: 140px;
  margin-right: 8px;
}
.rule-select {
  width: 240px;
}
.day-input {
  margin-left: 8px;
}
.inline-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 0 6px 0 12px;
}
.terms-form :deep(.el-form-item) {
  margin-bottom: 14px;
}
</style>
