<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>印花税填报</span>
          <el-button type="primary" size="small" :loading="saving" @click="submit">计算并保存</el-button>
        </div>
      </template>
      <p class="hint">税目税率（印花税法）+ 六税两费减半（12号公告至 2027-12-31）。</p>
      <el-form :model="form" label-width="110px" @submit.prevent>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="所属期">
              <el-date-picker v-model="form.period" type="month" value-format="YYYY-MM" placeholder="选择所属期" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="纳税人名称">
              <el-input v-model="form.taxpayer_name" placeholder="企业/个人名" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="六税两费减半">
              <el-switch v-model="form.half_discount" />
            </el-form-item>
          </el-col>
        </el-row>
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
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button size="small" text :disabled="!row.has_export" @click="download(row)">下载底稿</el-button>
            <el-button size="small" text type="primary" :disabled="row.has_export" @click="exportXlsx(row)">生成底稿</el-button>
            <el-button size="small" text type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'

const taxItems = ref([])
const form = ref({ period: '', taxpayer_name: '', half_discount: false, items: [] })
const list = ref([])
const saving = ref(false)
const resultTitle = ref('')

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

async function submit() {
  if (!form.value.period || !form.value.items.length) {
    ElMessage.warning('请先选所属期并至少加一行税目明细')
    return
  }
  saving.value = true
  try {
    const { data } = await api.post('/finance/stamp-duty/filings', form.value)
    const c = data.calc_result
    resultTitle.value = `应纳税额 ¥${c.total_tax}（减半前 ¥${c.raw_total}，节省 ¥${c.half_saved}）`
    await loadList()
  } catch { /* 拦截器已提示 */ } finally {
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
