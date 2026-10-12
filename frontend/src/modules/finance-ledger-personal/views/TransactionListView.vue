<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>
            交易流水<span v-if="total" class="total-hint">（共 {{ total }} 条）</span>
            <span v-if="total > items.length" class="total-hint warn">仅显示前 {{ items.length }} 条，共 {{ total }} 条，请用筛选缩小范围或导出 CSV 查看全部</span>
          </span>
          <div class="filters">
            <el-select v-model="filters.month" placeholder="月份" size="small" clearable style="width: 120px">
              <el-option v-for="m in monthOptions" :key="m" :label="m" :value="m" />
            </el-select>
            <el-select v-model="filters.account_id" placeholder="账户" size="small" clearable style="width: 130px">
              <el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" />
            </el-select>
            <el-select v-model="filters.category_id" placeholder="分类" size="small" clearable style="width: 130px">
              <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
            </el-select>
            <el-input v-model="filters.keyword" placeholder="备注关键词" size="small" clearable style="width: 150px" @keyup.enter="reload(1)" />
            <el-button size="small" type="primary" @click="reload(1)">筛选</el-button>
            <el-button size="small" @click="exportCsv">导出 CSV</el-button>
          </div>
        </div>
      </template>

      <ag-grid-vue
        style="width: 100%; height: 480px"
        class="ag-theme-alpine"
        :rowData="items"
        :columnDefs="columnDefs"
        :pagination="true"
        :paginationPageSize="20"
        :paginationPageSizeSelector="[20, 50, 100]"
        :localeText="localeText"
        :getRowId="getRowId"
      />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { AgGridVue } from 'ag-grid-vue3'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const accounts = ref([])
const categories = ref([])
const filters = reactive({ month: '', account_id: null, category_id: null, keyword: '' })

const monthOptions = computed(() => {
  const out = []
  const d = new Date()
  for (let i = 0; i < 12; i++) {
    out.push(new Date(d.getFullYear(), d.getMonth() - i, 1).toISOString().slice(0, 7))
  }
  return out
})

const columnDefs = [
  { field: 'occurred_on', headerName: '日期', width: 120 },
  {
    field: 'kind',
    headerName: '类型',
    width: 90,
    valueFormatter: (p) => (p.value === 'income' ? '收入' : '支出'),
  },
  { field: 'category_name', headerName: '分类', minWidth: 120 },
  { field: 'account_name', headerName: '账户', minWidth: 100 },
  {
    field: 'amount',
    headerName: '金额',
    width: 120,
    type: 'rightAligned',
    valueFormatter: (p) => Number(p.value).toFixed(2),
  },
  { field: 'note', headerName: '备注', minWidth: 160, flex: 1 },
]

const localeText = { noRowsToShow: '暂无流水', page: '页', of: '/', to: '至', more: '更多' }
const getRowId = (p) => String(p.data.id)

async function load() {
  const [acc, cat] = await Promise.all([
    api.get('/finance/ledger/personal/accounts'),
    api.get('/finance/ledger/personal/categories'),
  ])
  accounts.value = acc.data
  categories.value = cat.data
}

async function reload(page = 1) {
  loading.value = true
  try {
    const params = { page, page_size: 500 }
    if (filters.month) params.month = filters.month
    if (filters.account_id) params.account_id = filters.account_id
    if (filters.category_id) params.category_id = filters.category_id
    if (filters.keyword) params.keyword = filters.keyword
    const { data } = await api.get('/finance/ledger/personal/transactions', { params })
    items.value = data.items
    total.value = data.total
  } catch {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

function exportCsv() {
  const params = new URLSearchParams()
  if (filters.month) params.set('month', filters.month)
  if (filters.account_id) params.set('account_id', filters.account_id)
  if (filters.category_id) params.set('category_id', filters.category_id)
  if (filters.keyword) params.set('keyword', filters.keyword)
  window.open(`/api/finance/ledger/personal/export/csv?${params.toString()}`, '_blank')
}

onMounted(async () => {
  await load()
  await reload()
})
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.total-hint {
  color: #909399;
  font-size: 12px;
  font-weight: 400;
  margin-left: 8px;
}

.total-hint.warn {
  color: #e6a23c;
}

.filters {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
</style>
