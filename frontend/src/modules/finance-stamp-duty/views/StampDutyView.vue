<template>
  <div class="page">
    <HelpLink section="6.9" />
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>印花税填报</span>
          <el-button type="primary" size="small" :loading="saving" @click="submit">
            {{ revisingId ? '保存更正' : '计算并保存' }}
          </el-button>
        </div>
      </template>
      <p class="hint">税目税率（印花税法）+ 六税两费减半（12号公告至 2027-12-31）。</p>
      <el-alert v-if="revisingId" type="warning" :closable="false" class="mt8"
        :title="`正在更正记录 #${revisingId}：不允许更换纳税人或所属期，保存后由后端重算；已导出/已提交的记录后端会拒绝更正`" />
      <el-button v-if="revisingId" size="small" text class="mt8" @click="cancelRevise">取消更正</el-button>
      <el-form :model="form" label-width="110px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="所属期">
              <el-date-picker v-model="form.period" type="month" value-format="YYYY-MM"
                placeholder="选择所属期" :disabled="Boolean(revisingId)" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="纳税人名称">
              <el-input v-model="form.taxpayer_name" placeholder="企业/个人名" :disabled="Boolean(revisingId)" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="六税两费减半">
              <el-switch v-model="form.half_discount" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="申报周期">
          <el-radio-group v-model="form.filing_period" :disabled="Boolean(revisingId)">
            <el-radio value="month">按月/按次</el-radio>
            <el-radio value="quarter">按季（季末月）</el-radio>
          </el-radio-group>
          <span class="hint">按季申报的所属期须为季末月（03/06/09/12），防止同季不同月各建一条绕过防重</span>
        </el-form-item>
        <el-divider>税目明细</el-divider>
        <el-table :data="form.items" size="small" border>
          <el-table-column label="税目" width="280">
            <template #default="{ row }">
              <el-select v-model="row.tax_item" filterable placeholder="选择税目">
                <el-option v-for="t in taxItems" :key="t.tax_item" :value="t.tax_item" :label="`${t.tax_item}（${t.rate}‰）`" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="计税金额" width="200">
            <template #default="{ row }">
              <el-input-number v-model="row.amount" :min="0" :controls="false" style="width: 100%" />
            </template>
          </el-table-column>
          <el-table-column label="免税" width="80">
            <template #default="{ row }">
              <el-switch v-model="row.exempt" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80">
            <template #default="{ $index }">
              <el-button size="small" text type="danger" @click="removeItem($index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-button size="small" class="mt8" @click="addItem">+ 加一行</el-button>
      </el-form>
      <el-alert v-if="resultTitle" :title="resultTitle" type="success" :closable="false" class="mt8" />
    </el-card>

    <el-card shadow="never" class="mt8">
      <template #header><span>填报记录</span></template>
      <el-table :data="list" size="small" border>
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="period" label="所属期" width="100" />
        <el-table-column prop="taxpayer_name" label="纳税人" />
        <el-table-column label="应纳税额" width="120">
          <template #default="{ row }">¥{{ (row.calc_result?.total_tax ?? 0).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ row.status }}</el-tag>
          </template>
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

const taxItems = ref([])
const form = ref({ period: '', taxpayer_name: '', half_discount: false, filing_period: 'month', items: [] })
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

function blankForm() {
  return { period: '', taxpayer_name: '', half_discount: false, filing_period: 'month', items: [] }
}

async function loadItems() {
  try {
    const { data } = await api.get('/finance/stamp-duty/tax-items')
    taxItems.value = data
  } catch { /* 拦截器已提示 */ }
}

async function loadList() {
  try {
    const { data } = await api.get('/finance/stamp-duty/filings')
    list.value = data.items || []
  } catch { /* 拦截器已提示 */ }
}

function addItem() {
  form.value.items.push({ tax_item: '', amount: 0, exempt: false })
}

function removeItem(idx) {
  form.value.items.splice(idx, 1)
}

// 受控更正：回填原申报税目，提交走 PUT /filings/{id}；
// 已导出/已提交由后端 409 拒绝，提示交给拦截器，视图不重复 toast。
function startRevise(row) {
  form.value = {
    period: row.period,
    taxpayer_name: row.taxpayer_name || '',
    half_discount: Boolean(row.half_discount),
    // 更正保留原申报周期：季度记录不能被 PUT 默认值降级成 month
    filing_period: row.calc_result?.filing_period || 'month',
    items: (row.items || []).map((i) => ({ ...i })),
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
  if (!form.value.period || !form.value.items.length) {
    ElMessage.warning('请先选所属期并至少加一行税目明细')
    return
  }
  saving.value = true
  try {
    const revising = revisingId.value
    const { data } = revising
      ? await api.put(`/finance/stamp-duty/filings/${revising}`, form.value)
      : await api.post('/finance/stamp-duty/filings', form.value)
    if (revising) ElMessage.success('已更正并重算')
    const c = data.calc_result || {}
    resultTitle.value = `应纳税额 ¥${c.total_tax}（减半前 ¥${c.raw_total}，节省 ¥${c.half_saved}）`
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
    await api.post(`/finance/stamp-duty/filings/${row.id}/export`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

function download(row) {
  window.open(`${api.defaults.baseURL}/finance/stamp-duty/filings/${row.id}/download`, '_blank')
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除 ${row.period} 的印花税填报记录？删除后不可恢复。`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/finance/stamp-duty/filings/${row.id}`)
    await loadList()
  } catch { /* 拦截器已提示 */ }
}

onMounted(async () => {
  await loadItems()
  await loadList()
})
</script>

<style scoped>
.hint { margin: 0 0 12px; }
</style>
