<template>
  <div class="page">
    <HelpLink section="6.10" />
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>增值税填报（一般纳税人/小规模）</span>
          <el-button type="primary" size="small" :loading="saving" @click="submit">
            {{ revisingId ? '保存更正' : '计算并保存' }}
          </el-button>
        </div>
      </template>
      <el-alert v-if="revisingId" type="warning" :closable="false" class="mt8"
        :title="`正在更正记录 #${revisingId}：不允许更换纳税人或所属期，保存后由后端重算；已导出/已提交的记录后端会拒绝更正`" />
      <el-button v-if="revisingId" size="small" text class="mt8" @click="cancelRevise">取消更正</el-button>
      <el-form :model="form" label-width="110px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="6">
            <el-form-item label="所属期">
              <el-date-picker v-model="form.period" type="month" value-format="YYYY-MM"
                placeholder="所属期" :disabled="Boolean(revisingId)" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="纳税人类型">
              <el-radio-group v-model="form.taxpayer_type" :disabled="Boolean(revisingId)">
                <el-radio value="general">一般纳税人</el-radio>
                <el-radio value="small_scale">小规模</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="纳税人名称">
              <el-input v-model="form.taxpayer_name" :disabled="Boolean(revisingId)" />
            </el-form-item>
          </el-col>
        </el-row>
        <template v-if="form.taxpayer_type === 'general'">
          <el-divider>销项明细</el-divider>
          <el-table :data="form.output_items" size="small" border>
            <el-table-column label="业务类型" width="200">
              <template #default="{ row }">
                <el-input v-model="row.business_type" />
              </template>
            </el-table-column>
            <el-table-column label="销售额" width="180">
              <template #default="{ row }">
                <el-input-number v-model="row.amount" :min="0" :controls="false" style="width: 100%" />
              </template>
            </el-table-column>
            <el-table-column label="税率" width="120">
              <template #default="{ row }">
                <el-select v-model="row.rate">
                  <el-option :value="0.13" label="13%" />
                  <el-option :value="0.09" label="9%" />
                  <el-option :value="0.06" label="6%" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{ $index }">
                <el-button size="small" text type="danger" @click="form.output_items.splice($index, 1)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-button size="small" class="mt8" @click="form.output_items.push({ business_type: '销售货物', amount: 0, rate: 0.13 })">+ 加一行</el-button>
          <el-form-item label="进项税额" class="mt8" style="width: 260px">
            <el-input-number v-model="form.input_vat" :min="0" :controls="false" style="width: 100%" />
          </el-form-item>
          <el-form-item label="进项转出" style="width: 260px">
            <el-input-number v-model="form.input_transfer_out" :min="0" :controls="false" style="width: 100%" />
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="申报周期" style="width: 280px">
            <el-radio-group v-model="form.filing_period">
              <el-radio value="quarter">按季（30 万）</el-radio>
              <el-radio value="month">按月（10 万）</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item :label="form.filing_period === 'month' ? '月度销售额' : '季度销售额'" style="width: 280px">
            <el-input-number v-model="form.sales_amount" :min="0" :controls="false" style="width: 100%" />
          </el-form-item>
          <el-form-item label="征收率" style="width: 280px">
            <el-select v-model="form.levy_rate">
              <el-option :value="0.01" label="1%（3% 减按 1%）" />
              <el-option :value="0.03" label="3%" />
              <el-option :value="0.05" label="5%" />
            </el-select>
          </el-form-item>
          <el-alert type="info" :closable="false"
            :title="form.filing_period === 'month' ? '月度 ≤10 万免征；超 10 万全额按征收率计税。' : '季度 ≤30 万免征；超 30 万全额按征收率计税。'" />
        </template>
      </el-form>
      <el-alert v-if="resultTitle" :title="resultTitle" type="success" :closable="false" class="mt8" />
    </el-card>

    <el-card shadow="never" class="mt8">
      <template #header><span>填报记录</span></template>
      <el-table :data="list" size="small" border>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="period" label="所属期" width="100" />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">{{ row.taxpayer_type === 'general' ? '一般' : '小规模' }}</template>
        </el-table-column>
        <el-table-column label="应纳税额" width="130">
          <template #default="{ row }">¥{{ (row.calc_result?.payable ?? 0).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280">
          <template #default="{ row }">
            <el-button size="small" text :disabled="!row.has_export" @click="download(row)">下载底稿</el-button>
            <el-button size="small" text type="primary" :disabled="row.has_export" @click="exportXlsx(row)">生成底稿</el-button>
            <el-button size="small" text type="primary" :disabled="reviseBlocked(row)" :title="reviseHint(row)" @click="startRevise(row)">更正</el-button>
            <el-button size="small" text type="danger" :disabled="deleteBlocked(row)" :title="deleteBlocked(row) ? '已提交的申报不可删除' : ''" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import HelpLink from '../../../components/HelpLink.vue'
import api from '../../../api'

function blankForm() {
  return {
    period: '', taxpayer_type: 'general', taxpayer_name: '',
    output_items: [], input_vat: 0, input_transfer_out: 0,
    sales_amount: 0, levy_rate: 0.01, filing_period: 'quarter',
  }
}

const form = ref(blankForm())
const list = ref([])
const saving = ref(false)
const resultTitle = ref('')
const revisingId = ref(null)

// N-6：已提交/已导出不允许更正（后端 ensure_revisable 同口径），已提交不可删除；
// 已导出底稿仍允许删除后重填（与后端删除口径一致）。
function reviseBlocked(row) {
  return Boolean(row) && (row.status === 'submitted' || Boolean(row.has_export))
}

function deleteBlocked(row) {
  return Boolean(row) && row.status === 'submitted'
}

function reviseHint(row) {
  if (!row) return ''
  if (row.status === 'submitted') return '已提交，不能更正或删除；如需变更请联系主管税务机关'
  if (row.has_export) return '已导出底稿，不能直接更正；请先删除该记录后重新填报'
  return ''
}

async function loadList() {
  try {
    const { data } = await api.get('/finance/vat/filings')
    list.value = data.items || []
  } catch { /* 拦截器已提示 */ }
}

// 受控更正：把原填报回填到表单，提交走 PUT /filings/{id}；
// 已导出/已提交由后端 409 拒绝，提示交给拦截器，视图不重复 toast。
function startRevise(row) {
  const inputs = row.inputs || {}
  form.value = {
    period: row.period,
    taxpayer_type: row.taxpayer_type,
    taxpayer_name: row.taxpayer_name || '',
    output_items: (inputs.output_items || []).map((i) => ({ ...i })),
    input_vat: inputs.input_vat ?? 0,
    input_transfer_out: inputs.input_transfer_out ?? 0,
    sales_amount: inputs.sales_amount ?? 0,
    levy_rate: inputs.levy_rate ?? 0.01,
    filing_period: inputs.filing_period || 'quarter',
  }
  revisingId.value = row.id
  resultTitle.value = ''
}

function cancelRevise() {
  revisingId.value = null
  form.value = blankForm()
  resultTitle.value = ''
}

async function submit() {
  if (!form.value.period) {
    ElMessage.warning('请先选择所属期')
    return
  }
  saving.value = true
  try {
    const revising = revisingId.value
    const { data } = revising
      ? await api.put(`/finance/vat/filings/${revising}`, form.value)
      : await api.post('/finance/vat/filings', form.value)
    if (revising) ElMessage.success('已更正并重算')
    const c = data.calc_result || {}
    if (data.taxpayer_type === 'general') {
      resultTitle.value = `销项 ¥${c.output_vat} − 进项 ¥${c.input_vat} + 转出 ¥${c.input_transfer_out || 0} = 应纳税额 ¥${c.payable}` +
        (c.carryforward_vat ? `（期末留抵 ¥${c.carryforward_vat}）` : '')
    } else {
      const label = c.filing_period === 'month' ? '月度' : '季度'
      resultTitle.value = c.exempt
        ? `${label}销售额未超 ${c.exempt_threshold / 10000} 万，免征`
        : `应纳税额 ¥${c.payable}`
    }
    revisingId.value = null
    await loadList()
  } catch {
    /* 拦截器已提示；已导出/已提交 409 保持更正态，便于按文案调整或删除重填 */
  } finally {
    saving.value = false
  }
}

async function exportXlsx(row) {
  try {
    await api.post(`/finance/vat/filings/${row.id}/export`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

function download(row) {
  window.open(`${api.defaults.baseURL}/finance/vat/filings/${row.id}/download`, '_blank')
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除 ${row.period} 的增值税填报记录？删除后不可恢复。`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/finance/vat/filings/${row.id}`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

onMounted(loadList)
</script>

<style scoped>
</style>
