<template>
  <div class="panel">
    <el-card shadow="never" class="sub">
      <template #header>
        <div class="card-head">
          <span>合同信息</span>
          <span class="head-actions">
            <el-tag size="small" :type="contractStatusMeta(contract?.status).type">
              {{ contract?.status_label || contractStatusMeta(contract?.status).label }}
            </el-tag>
            <el-button
              size="small"
              type="primary"
              :loading="savingContract"
              :disabled="!canEdit"
              @click="saveContract"
            >
              保存合同
            </el-button>
          </span>
        </div>
      </template>
      <el-alert
        v-if="contract && !canEdit"
        :title="`合同「${contract.contract_no}」已归档：合同信息与付款计划只读，如需变动请新建合同。`"
        type="info"
        :closable="false"
        class="mb8"
      />
      <el-form :model="form" label-width="110px" @submit.prevent>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="合同号">
              <el-input v-model="form.contract_no" :disabled="!canEdit" maxlength="64" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="合同名称">
              <el-input v-model="form.name" :disabled="!canEdit" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="承租人主体">
              <el-input v-model="form.lessee" :disabled="!canEdit" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="项目/资产组">
              <el-input v-model="form.project" :disabled="!canEdit" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="出租方">
              <el-input v-model="form.lessor" :disabled="!canEdit" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="年折现率">
              <el-input v-model="form.annual_rate" :disabled="!canEdit" placeholder="小数，例如 3.5% 填 0.035" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="租赁起始日">
              <el-date-picker
                v-model="form.start_date"
                type="date"
                value-format="YYYY-MM-DD"
                :disabled="!canEdit"
                class="full-width"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="租赁截止日">
              <el-date-picker
                v-model="form.end_date"
                type="date"
                value-format="YYYY-MM-DD"
                :disabled="!canEdit"
                class="full-width"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="利率依据">
          <el-input v-model="form.rate_basis" :disabled="!canEdit" maxlength="2000" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" :disabled="!canEdit" maxlength="2000" />
        </el-form-item>
        <el-form-item label="附件依据">
          <el-input v-model="form.attachment_note" :disabled="!canEdit" maxlength="2000" />
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="sub">
      <template #header>
        <div class="card-head">
          <span>付款计划（整批替换）</span>
          <span class="head-actions">
            <span class="muted">合计 {{ totals.amount }} 元 / {{ totals.months }} 个月</span>
            <el-button size="small" :disabled="!canEdit" @click="addPayment">加一行</el-button>
            <el-button size="small" :disabled="!canEdit" @click="openImport">导入 xlsx</el-button>
            <el-button
              size="small"
              type="primary"
              :loading="savingPayments"
              :disabled="!canEdit"
              @click="savePayments"
            >
              保存付款计划
            </el-button>
          </span>
        </div>
      </template>
      <div class="tpl-row"><TemplateLinks type="lease" /></div>
      <el-table
        :data="payments"
        size="small"
        border
        empty-text="暂无付款行：可「加一行」手工录入，或「导入 xlsx」预览确认后整批替换"
      >
        <el-table-column label="期次" width="80">
          <template #default="{ row }">
            <el-input-number v-model="row.sequence" :min="1" :step="1" :controls="false" :disabled="!canEdit" class="num" />
          </template>
        </el-table-column>
        <el-table-column label="计租开始" width="150">
          <template #default="{ row }">
            <el-date-picker
              v-model="row.period_start"
              type="date"
              value-format="YYYY-MM-DD"
              :disabled="!canEdit"
              placeholder="YYYY-MM-DD"
              class="full-width"
            />
          </template>
        </el-table-column>
        <el-table-column label="计租结束" width="150">
          <template #default="{ row }">
            <el-date-picker
              v-model="row.period_end"
              type="date"
              value-format="YYYY-MM-DD"
              :disabled="!canEdit"
              placeholder="YYYY-MM-DD"
              class="full-width"
            />
          </template>
        </el-table-column>
        <el-table-column label="覆盖月数" width="100">
          <template #default="{ row }">
            <el-input-number
              v-model="row.months"
              :min="0"
              :max="1200"
              :step="1"
              :controls="false"
              :disabled="!canEdit"
              :value-on-clear="null"
              class="num"
            />
          </template>
        </el-table-column>
        <el-table-column label="付款金额" width="130">
          <template #default="{ row }">
            <el-input-number
              v-model="row.amount"
              :min="0"
              :precision="2"
              :controls="false"
              :disabled="!canEdit"
              class="num"
            />
          </template>
        </el-table-column>
        <el-table-column label="起租首期" width="86">
          <template #default="{ row }">
            <el-switch v-model="row.start_flag" :disabled="!canEdit" />
          </template>
        </el-table-column>
        <el-table-column label="期初支付" width="86">
          <template #default="{ row }">
            <el-switch v-model="row.prepay_flag" :disabled="!canEdit" />
          </template>
        </el-table-column>
        <el-table-column label="免租期" width="76">
          <template #default="{ row }">
            <el-switch v-model="row.is_free_rent" :disabled="!canEdit" />
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="140">
          <template #default="{ row }">
            <el-input v-model="row.note" :disabled="!canEdit" maxlength="500" placeholder="如 免租期 / 末期 0 月期" />
          </template>
        </el-table-column>
        <el-table-column label="源行号" width="80">
          <template #default="{ row }">
            <span class="muted">{{ row.source_row ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70" fixed="right">
          <template #default="{ $index }">
            <el-button size="small" link type="danger" :disabled="!canEdit" @click="removePayment($index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p class="hint">
        免租期、0 月期与期初支付都是正常业务行；保存与导入都不会静默删除。月数留空表示由引擎按计租起止推导；期次按行顺序保存为 1..N。
      </p>
    </el-card>

    <el-dialog v-model="uploadDialog" title="导入付款计划（预览不落库，确认时才整批替换）" width="820px">
      <el-upload
        :auto-upload="false"
        :limit="1"
        :file-list="fileList"
        accept=".xlsx"
        :on-change="onFileChange"
        :on-remove="onFileRemove"
      >
        <el-button size="small">选择 xlsx</el-button>
        <template #tip>
          <div class="hint">按页签/表头识别期次、计租起止、金额与月数；换文件请先移除已选文件。</div>
        </template>
      </el-upload>
      <div class="tpl-row"><TemplateLinks type="lease" /></div>
      <div class="preview-actions">
        <el-button size="small" type="primary" :loading="previewing" :disabled="!importFile" @click="previewFile">
          预览解析
        </el-button>
        <el-button v-if="preview" size="small" @click="clearPreview">重新选择</el-button>
      </div>

      <template v-if="preview">
        <el-alert
          :title="previewSummaryText"
          :type="previewHasErrors(preview) ? 'error' : 'info'"
          :closable="false"
          class="mt8"
        />
        <el-table :data="previewRowList" size="small" border max-height="280" class="mt8">
          <el-table-column prop="sequence" label="期次" width="66" />
          <el-table-column prop="source_row" label="源行" width="66" />
          <el-table-column prop="period_start" label="计租开始" width="106" />
          <el-table-column prop="period_end" label="计租结束" width="106" />
          <el-table-column prop="months" label="月数" width="72" />
          <el-table-column prop="amount" label="付款金额" width="110" />
          <el-table-column label="标记" min-width="130">
            <template #default="{ row }">
              <el-tag v-if="row.start_flag" size="small" type="info" class="mr4">起租首期</el-tag>
              <el-tag v-if="row.prepay_flag" size="small" type="info" class="mr4">期初支付</el-tag>
              <el-tag v-if="isZeroRow(row)" size="small" type="warning">免租/0 月期</el-tag>
              <span v-if="!row.start_flag && !row.prepay_flag && !isZeroRow(row)" class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="note" label="备注" min-width="120" />
        </el-table>
        <div v-if="previewErrorList.length" class="issue-box">
          <p class="hint danger">错误（预览不落库；存在错误时不能确认导入）：</p>
          <ul class="issue-list">
            <li v-for="(message, index) in previewErrorList" :key="`e-${index}`">{{ message }}</li>
          </ul>
        </div>
        <div v-if="previewWarningList.length" class="issue-box">
          <p class="hint">提示（确认前会再次要求确认）：</p>
          <ul class="issue-list">
            <li v-for="(message, index) in previewWarningList" :key="`w-${index}`">{{ message }}</li>
          </ul>
        </div>
      </template>

      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="applying" :disabled="!canApply" @click="applyPreview">
          确认导入并替换付款计划
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import TemplateLinks from '../../../components/TemplateLinks.vue'
import {
  buildContractPayload,
  buildPaymentsPayload,
  contractStatusMeta,
  contractToForm,
  emptyPaymentRow,
  leaseApi,
  paymentsToEditor,
  paymentsTotals as sumPayments,
  previewErrors,
  previewHasErrors,
  previewRows,
  previewWarnings,
  previewZeroRows,
  renumberPayments,
} from '../api'

const props = defineProps({
  contract: { type: Object, default: null },
})
const emit = defineEmits(['refresh'])

const form = reactive(contractToForm(props.contract))
const canEdit = computed(() => props.contract?.status === 'draft')
const savingContract = ref(false)
const payments = ref([])
const paymentsLoading = ref(false)
const savingPayments = ref(false)

const totals = computed(() => sumPayments(payments.value))

async function loadPayments() {
  if (!props.contract?.id) return
  paymentsLoading.value = true
  try {
    const { data } = await leaseApi.payments(props.contract.id)
    payments.value = paymentsToEditor(data)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    paymentsLoading.value = false
  }
}

async function saveContract() {
  if (!canEdit.value) {
    ElMessage.warning('合同已归档，只读：如需修改请新建合同')
    return
  }
  let payload
  try {
    payload = buildContractPayload(form)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  savingContract.value = true
  try {
    await leaseApi.patchContract(props.contract.id, payload)
    ElMessage.success('合同信息已保存')
    emit('refresh')
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    savingContract.value = false
  }
}

function addPayment() {
  payments.value.push(emptyPaymentRow(payments.value.length + 1))
}

function removePayment(index) {
  payments.value.splice(index, 1)
  renumberPayments(payments.value)
}

async function savePayments() {
  if (!canEdit.value) {
    ElMessage.warning('合同已归档，付款计划只读')
    return
  }
  let payload
  try {
    payload = buildPaymentsPayload(payments.value)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  if (!payload.items.length) {
    try {
      await ElMessageBox.confirm(
        '当前没有付款行，确认清空整份付款计划？清空后测算会因空计划被拒绝。',
        '清空付款计划',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  savingPayments.value = true
  try {
    const { data } = await leaseApi.putPayments(props.contract.id, payload)
    ElMessage.success(`付款计划已保存（${(data || []).length ?? payload.items.length} 行，整批替换）`)
    await loadPayments()
    emit('refresh')
  } catch {
    /* 拦截器已统一提示；期数冲突等由后端返回中文 409 */
  } finally {
    savingPayments.value = false
  }
}

// ---------- 导入：预览不落库，确认时重新上传同一文件才落库 ----------
const uploadDialog = ref(false)
const importFile = ref(null)
const fileList = ref([])
const preview = ref(null)
const previewing = ref(false)
const applying = ref(false)

function clearPreview() {
  importFile.value = null
  fileList.value = []
  preview.value = null
}

function openImport() {
  if (!canEdit.value) {
    ElMessage.warning('合同已归档，不能导入付款计划')
    return
  }
  clearPreview()
  uploadDialog.value = true
}

function onFileChange(file) {
  importFile.value = file || null
  fileList.value = file ? [file] : []
  preview.value = null
}

function onFileRemove() {
  clearPreview()
}

function buildImportFormData() {
  const formData = new FormData()
  formData.append('file', importFile.value.raw, importFile.value.name || 'payments.xlsx')
  return formData
}

async function previewFile() {
  if (!props.contract?.id) return
  if (!importFile.value?.raw) {
    ElMessage.warning('请先选择要导入的 xlsx 文件')
    return
  }
  previewing.value = true
  try {
    const { data } = await leaseApi.previewImport(props.contract.id, buildImportFormData())
    preview.value = data
    if (!previewRows(data).length) {
      ElMessage.warning('预览未解析到付款行：请检查页签与表头是否符合模板')
    }
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    previewing.value = false
  }
}

const previewRowList = computed(() => previewRows(preview.value))
const previewWarningList = computed(() => previewWarnings(preview.value))
const previewErrorList = computed(() => previewErrors(preview.value))

function isZeroRow(row) {
  const zero = (value) => value !== null && value !== undefined && value !== '' && Number(value) === 0
  return zero(row?.amount) || zero(row?.months) || Boolean(row?.is_free_rent)
}

const previewSummaryText = computed(() => {
  if (!preview.value) return ''
  const sheet = preview.value.sheet_name ? `页签「${preview.value.sheet_name}」` : ''
  const errors = previewErrorList.value.length
  const warnings = previewWarningList.value.length
  const zero = previewZeroRows(preview.value)
  return `${sheet}解析 ${previewRowList.value.length} 行付款期；错误 ${errors} 条、提示 ${warnings} 条、免租/0 月期 ${zero} 行（原样保留）。预览不落库，确认导入时才整批替换。`
})

const canApply = computed(
  () => Boolean(importFile.value?.raw) && Boolean(preview.value) && !previewHasErrors(preview.value),
)

async function applyPreview() {
  if (!props.contract?.id || !preview.value) return
  if (!importFile.value?.raw) {
    ElMessage.warning('导入文件已不在内存：请重新选择文件并预览后再确认')
    return
  }
  if (previewHasErrors(preview.value)) {
    ElMessage.warning('预览存在错误：修正源文件并重新预览后才能导入')
    return
  }
  const zeroRows = previewZeroRows(preview.value)
  const warnings = previewWarningList.value
  if (zeroRows || warnings.length) {
    const parts = []
    if (zeroRows) parts.push(`含 ${zeroRows} 行免租期/0 月期（将原样保留）`)
    if (warnings.length) parts.push(`含 ${warnings.length} 条提示`)
    try {
      await ElMessageBox.confirm(
        `${parts.join('；')}。确认导入会整批替换当前付款计划，是否继续？`,
        '导入确认',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  applying.value = true
  try {
    const { data } = await leaseApi.applyImport(props.contract.id, buildImportFormData())
    const written = (data?.items || []).length
    ElMessage.success(`付款计划已导入（${written || previewRowList.value.length} 行，整批替换）`)
    uploadDialog.value = false
    clearPreview()
    await loadPayments()
    emit('refresh')
  } catch {
    /* 拦截器已统一提示；导入校验失败由后端返回中文 400/409 */
  } finally {
    applying.value = false
  }
}

onMounted(loadPayments)

defineExpose({
  form,
  canEdit,
  savingContract,
  saveContract,
  payments,
  paymentsLoading,
  savingPayments,
  totals,
  loadPayments,
  addPayment,
  removePayment,
  savePayments,
  uploadDialog,
  importFile,
  fileList,
  preview,
  previewing,
  applying,
  clearPreview,
  openImport,
  onFileChange,
  onFileRemove,
  previewFile,
  previewRowList,
  previewWarningList,
  previewErrorList,
  previewSummaryText,
  canApply,
  isZeroRow,
  applyPreview,
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
  margin: 8px 0 0;
}
.danger {
  color: var(--el-color-danger);
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.full-width {
  width: 100%;
}
.num {
  width: 100%;
}
.preview-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}
.tpl-row {
  margin-bottom: 8px;
}
.issue-box {
  margin-top: 10px;
}
.issue-list {
  margin: 4px 0 0;
  padding-left: 20px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  max-height: 120px;
  overflow: auto;
}
.mb8 {
  margin-bottom: 8px;
}
.mt8 {
  margin-top: 8px;
}
.mr4 {
  margin-right: 4px;
}
</style>
