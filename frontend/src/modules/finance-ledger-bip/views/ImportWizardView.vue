<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>BIP 导出导入向导</span>
        </div>
      </template>
      <el-steps :active="step" simple finish-status="success">
        <el-step title="上传文件" />
        <el-step title="类型识别与映射" />
        <el-step title="确认入库" />
      </el-steps>

      <div v-if="step === 0" class="step-body">
        <input type="file" accept=".xlsx,.xls" @change="onFile" />
        <p class="hint">支持三类用友 BIP 导出：凭证列表 / 辅助明细账 / 科目余额表（xlsx/xls，上限 10MB）。</p>
      </div>

      <div v-else-if="step === 1 && preview" class="step-body">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="文件">{{ preview.filename }}</el-descriptions-item>
          <el-descriptions-item label="识别类型">
            <el-tag :type="preview.file_type === 'unknown' ? 'danger' : 'success'" size="small">
              {{ preview.type_label }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="总行数">{{ preview.row_count }}</el-descriptions-item>
          <el-descriptions-item v-if="preview.file_type === 'balance' || form.file_type === 'balance'" label="会计期间">
            <el-input v-model="form.period" placeholder="YYYY-MM" size="small" style="width: 120px" />
          </el-descriptions-item>
        </el-descriptions>

        <template v-if="preview.file_type === 'unknown'">
          <p class="warn">未能自动识别类型，请人工指定：</p>
          <el-radio-group v-model="form.file_type">
            <el-radio-button value="voucher">凭证列表</el-radio-button>
            <el-radio-button value="aux">辅助明细账</el-radio-button>
            <el-radio-button value="balance">科目余额表</el-radio-button>
          </el-radio-group>
        </template>

        <h4>列映射（规范字段 → 实际列）</h4>
        <el-select
          v-for="f in mappingFieldList"
          :key="f"
          v-model="form.mapping[f]"
          size="small"
          clearable
          style="width: 100%; margin-bottom: 8px"
        >
          <template #prepend>{{ fieldLabels[f] || f }}</template>
          <el-option v-for="c in preview.columns" :key="c" :label="c" :value="c" />
        </el-select>

        <h4>预览（前 {{ preview.preview_rows.length }} 行）</h4>
        <el-table :data="preview.preview_rows" size="small" border max-height="260">
          <el-table-column v-for="c in preview.columns" :key="c" :prop="c" :label="c" min-width="110" show-overflow-tooltip />
        </el-table>
      </div>

      <div v-else-if="step === 2 && confirmResult" class="step-body">
        <el-result icon="success" :title="confirmResult.message">
          <template #sub-title>
            <p v-for="w in confirmResult.warnings" :key="w" class="warn">{{ w }}</p>
          </template>
        </el-result>
        <el-button type="primary" @click="goVouchers">去查询凭证</el-button>
        <el-button @click="reset">再导入一个</el-button>
      </div>

      <div class="step-footer">
        <el-button v-if="step > 0 && step < 2" @click="step -= 1">上一步</el-button>
        <el-button v-if="step === 1" type="primary" :disabled="!canConfirm" :loading="submitting" @click="doConfirm">确认入库</el-button>
      </div>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header><span>导入批次</span></template>
      <el-table :data="batches" size="small" empty-text="暂无批次">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="filename" label="文件" min-width="160" show-overflow-tooltip />
        <el-table-column prop="type_label" label="类型" width="110" />
        <el-table-column prop="period" label="期间" width="90" />
        <el-table-column prop="inserted_vouchers" label="凭证" width="80" align="right" />
        <el-table-column prop="inserted_entries" label="分录" width="80" align="right" />
        <el-table-column prop="inserted_balances" label="余额行" width="80" align="right" />
        <el-table-column prop="created_at" label="时间" width="170" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../../api'
import { takeFile } from '../../_shared/fileInput'
import { canConfirmImport, mappingFields } from '../importForm'

const router = useRouter()
const loading = ref(false)
const submitting = ref(false)
const step = ref(0)
const preview = ref(null)
const confirmResult = ref(null)
const batches = ref([])

const fieldLabels = {
  date: '日期', ledger: '核算账簿', voucher_no: '凭证号', summary: '摘要',
  debit: '借方金额', credit: '贷方金额', voucher_full_no: '会计凭证号',
  subject_code: '科目编码', subject_name: '科目名称',
  opening_debit: '期初借方', opening_credit: '期初贷方',
  current_debit: '本期借方', current_credit: '本期贷方',
  ending_debit: '期末借方', ending_credit: '期末贷方',
}

const form = reactive({ file: null, file_type: '', period: '', mapping: {} })

// 未识别类型按用户指定的类型给出手动映射清单；自动识别类型用预览下发的 required_fields
const mappingFieldList = computed(() => mappingFields(preview.value, form.file_type))

const canConfirm = computed(() => canConfirmImport(preview.value, form))

async function onFile(e) {
  const file = takeFile(e)
  if (!file) return
  form.file = file
  loading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const { data } = await api.post('/finance/ledger/bip/imports/preview', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    preview.value = data
    form.file_type = data.file_type === 'unknown' ? '' : data.file_type
    form.mapping = { ...(data.mapping || {}) }
    form.period = ''
    step.value = 1
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

async function doConfirm() {
  submitting.value = true
  try {
    const payload = { token: preview.value.token, mapping: form.mapping }
    const ftype = form.file_type || preview.value.file_type
    if (ftype) payload.file_type = ftype
    if (preview.value.file_type === 'balance' || ftype === 'balance') payload.period = form.period
    const { data } = await api.post('/finance/ledger/bip/imports/confirm', payload)
    confirmResult.value = data
    step.value = 2
    await loadBatches()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    submitting.value = false
  }
}

function goVouchers() {
  router.push({ name: 'finance-ledger-bip-vouchers' })
}

function reset() {
  step.value = 0
  preview.value = null
  confirmResult.value = null
  form.file = null
  form.file_type = ''
  form.period = ''
  form.mapping = {}
}

async function loadBatches() {
  try {
    const { data } = await api.get('/finance/ledger/bip/imports')
    batches.value = data.items || []
  } catch { /* 忽略 */ }
}

onMounted(loadBatches)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.step-body {
  margin-top: 20px;
}

.step-footer {
  margin-top: 20px;
  display: flex;
  gap: 8px;
}

.hint,
.warn {
  color: #e6a23c;
  font-size: 13px;
}
</style>
