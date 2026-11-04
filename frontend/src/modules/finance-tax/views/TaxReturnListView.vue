<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>申报记录</span>
          <el-button
            v-for="entry in taxPageEntries"
            :key="entry.name"
            size="small"
            @click="$router.push({ name: entry.name })"
          >
            {{ entry.title }}
          </el-button>
          <el-select v-model="enterpriseId" placeholder="全部企业" size="small" clearable style="width: 220px" @change="reload">
            <el-option v-for="e in enterprises" :key="e.id" :label="e.name" :value="e.id" />
          </el-select>
        </div>
      </template>
      <el-table :data="items" size="small" empty-text="暂无申报记录（在「税额计算」页保存生成）">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column label="企业" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ enterpriseName(row.enterprise_id) }}</template>
        </el-table-column>
        <el-table-column prop="year" label="年度" width="80" />
        <el-table-column prop="quarter" label="季度" width="70">
          <template #default="{ row }">{{ row.quarter ? `Q${row.quarter}` : '年度' }}</template>
        </el-table-column>
        <el-table-column prop="return_type" label="类型" width="80" />
        <el-table-column label="适用优惠" width="120">
          <template #default="{ row }">{{ row.tax_type || '—' }}</template>
        </el-table-column>
        <el-table-column label="应纳税所得额（元）" width="150" align="right">
          <template #default="{ row }">{{ row.taxable_income?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="应纳税额（元）" width="140" align="right">
          <template #default="{ row }">{{ row.tax_amount?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'submitted' ? 'success' : 'info'" size="small">
              {{ row.status === 'submitted' ? '已提交' : '草稿' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="330" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="$router.push({ name: 'finance-tax-return-detail', params: { id: row.id } })">预览</el-button>
            <el-button v-if="canEdit(row)" size="small" link type="primary" @click="openEdit(row)">更正</el-button>
            <el-button v-if="canEdit(row)" size="small" link type="primary" @click="submit(row)">提交</el-button>
            <el-button v-if="canDelete(row)" size="small" link type="danger" @click="remove(row)">删除</el-button>
            <el-button size="small" link type="primary" :disabled="!canExportA200000(row)" :title="canExportA200000(row) ? '' : exportBlockedHint(row)" @click="exportFile(row.id, 'excel')">Excel</el-button>
            <el-button size="small" link type="primary" :disabled="!canExportA200000(row)" :title="canExportA200000(row) ? '' : exportBlockedHint(row)" @click="exportFile(row.id, 'pdf')">PDF</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p class="hint">年度申报记录在 A100000 年度申报表接入前不可导出预缴表；历史缺季度的预缴记录请用「更正」补季度；已提交记录不可更正或删除。</p>
    </el-card>

    <el-dialog v-model="editVisible" title="更正申报记录（草稿）" width="620px">
      <el-form :model="editForm" label-width="130px">
        <el-row :gutter="12">
          <el-col :span="8"><el-form-item label="年度"><el-input-number v-model="editForm.tax_period" :controls="false" :precision="0" :min="2000" :max="2100" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8">
            <el-form-item label="季度">
              <el-select v-model="editForm.quarter" style="width: 100%" placeholder="请选择">
                <el-option v-for="q in ['Q1', 'Q2', 'Q3', 'Q4']" :key="q" :value="q" :label="q" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="申报类型">
              <el-select v-model="editForm.report_type" style="width: 100%">
                <el-option value="A类" label="A类（季度预缴）" />
                <el-option value="B类" label="B类（年度）" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="营业收入(万)"><el-input-number v-model="editForm.total_revenue" :controls="false" :step="0.01" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="其他收入(万)"><el-input-number v-model="editForm.other_income" :controls="false" :step="0.01" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="营业成本(万)"><el-input-number v-model="editForm.total_costs" :controls="false" :step="0.01" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="期间费用(万)"><el-input-number v-model="editForm.total_expenses" :controls="false" :step="0.01" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="纳税调整(万)"><el-input-number v-model="editForm.tax_adjustments" :controls="false" :step="0.01" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="税收优惠(万)"><el-input-number v-model="editForm.tax_preferential" :controls="false" :step="0.01" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="优惠选择">
          <el-select v-model="editForm.preferential_choice" style="width: 100%">
            <el-option value="" label="不指定（仅一种优惠时自动适用）" />
            <el-option value="high_tech" label="高新技术企业 15%" />
            <el-option value="small_micro" label="小型微利企业 5%" />
          </el-select>
        </el-form-item>
        <p class="hint">
          金额单位为万元，可填到 6 位小数（精确到分）；A类预缴必须填季度（历史缺季度记录在此补填后即可导出）；
          季报不纳入纳税调整，调整留待年度汇算。
        </p>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存更正</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script>
// 统计分析页已有路由但侧栏不可见，在申报记录页提供可见入口（审计 F04）。
// 入口按路由 name 跳转；name 由模块 manifest 注册，测试校验均指向已注册路由。
export const TAX_PAGE_ENTRIES = [{ name: 'finance-tax-statistics', title: '统计分析' }]
</script>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'
import { canDelete, canEdit, canExportA200000, editFormFrom, editPayloadFrom, exportBlockedHint } from '../returnActions'

const loading = ref(false)
const saving = ref(false)
const items = ref([])
const enterprises = ref([])
const enterpriseId = ref(undefined)
const taxPageEntries = TAX_PAGE_ENTRIES

const editVisible = ref(false)
const editTarget = ref(null)
const editForm = reactive(editFormFrom({}))

const enterpriseName = (id) => enterprises.value.find((e) => e.id === id)?.name || `#${id}`

async function reload() {
  loading.value = true
  try {
    const params = {}
    if (enterpriseId.value) params.enterprise_id = enterpriseId.value
    const { data } = await api.get('/finance/tax/tax-returns/', { params })
    items.value = data
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function openEdit(row) {
  editTarget.value = row
  Object.assign(editForm, editFormFrom(row))
  editVisible.value = true
}

async function saveEdit() {
  if (!editTarget.value) return
  saving.value = true
  try {
    await api.put(
      `/finance/tax/tax-returns/${editTarget.value.id}`,
      editPayloadFrom(editForm, editTarget.value.enterprise_id),
    )
    ElMessage.success('已保存更正')
    editVisible.value = false
    await reload()
  } catch {
    // 拦截器已提示；失败保持弹窗便于修改后重试
  } finally {
    saving.value = false
  }
}

async function submit(row) {
  try {
    await ElMessageBox.confirm(`提交 ${row.year} 年申报记录？提交后不可修改或删除。`, '提交确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  try {
    await api.put(`/finance/tax/tax-returns/${row.id}/submit`)
  } catch {
    return  // 拦截器已提示，提交失败不报成功
  }
  ElMessage.success('已提交')
  await reload()
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除 ${row.year} 年该条草稿申报记录？删除后不可恢复。`, '删除确认', { type: 'warning' })
  } catch {
    return  // 用户取消
  }
  try {
    await api.delete(`/finance/tax/tax-returns/${row.id}`)
  } catch {
    return  // 拦截器已提示
  }
  ElMessage.success('已删除')
  await reload()
}

function exportFile(id, kind) {
  window.open(`${api.defaults.baseURL}/finance/tax/tax-returns/${id}/export/${kind}`, '_blank')
}

onMounted(async () => {
  try {
    const { data } = await api.get('/finance/tax/enterprises/')
    enterprises.value = data
  } catch {
    // 拦截器已提示；企业列表拉不到时筛选项留空，仍继续加载申报列表
  }
  await reload()
})
</script>

<style scoped>
.hint {
  margin: 12px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
