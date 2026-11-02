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
            <el-button size="small" type="primary" plain :loading="importing" @click="pickCsv">导入 CSV</el-button>
            <input ref="csvInput" type="file" accept=".csv" class="hidden-file" @change="onCsvPicked" />
            <el-button size="small" @click="exportCsv">导出 CSV</el-button>
            <TemplateLinks type="personal" />
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
        :modules="gridModules"
        :getRowId="getRowId"
      />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { AgGridVue } from 'ag-grid-vue3'
import { LocaleModule, PaginationModule } from 'ag-grid-community'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'
import TemplateLinks from '../../../components/TemplateLinks.vue'
import { buildMonthOptions } from '../../_shared/dates'

// 金额统一安全格式化：后端字段可能为 null/undefined（未回填、导入失败行），
// 直接 toFixed 会抛错导致整页渲染崩溃，统一显示「—」占位
function fmtMoney(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}

const loading = ref(false)
const items = ref([])
const total = ref(0)
const accounts = ref([])
const categories = ref([])
const filters = reactive({ month: '', account_id: null, category_id: null, keyword: '' })

// ag-grid 36 不再默认注册功能模块：localeText / 分页需显式提供，否则中文空态不生效且控制台报 error #200
const gridModules = [LocaleModule, PaginationModule]

const monthOptions = computed(() => buildMonthOptions())

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
    valueFormatter: (p) => fmtMoney(p.value),
  },
  { field: 'note', headerName: '备注', minWidth: 160, flex: 1 },
]

const localeText = { noRowsToShow: '暂无流水', page: '页', of: '/', to: '至', more: '更多' }
const getRowId = (p) => String(p.data.id)

async function load() {
  try {
    const [acc, cat] = await Promise.all([
      api.get('/finance/ledger/personal/accounts'),
      api.get('/finance/ledger/personal/categories'),
    ])
    accounts.value = acc.data ?? []
    categories.value = cat.data ?? []
  } catch {
    /* 拦截器已统一提示；筛选下拉留空即可，不阻塞 onMounted 继续加载流水 */
  }
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
    items.value = data.items ?? []
    total.value = data.total ?? 0
  } catch {
    /* 拦截器已统一提示 */
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
  window.open(`${api.defaults.baseURL}/finance/ledger/personal/export/csv?${params.toString()}`, '_blank')
}

// CSV 导入（后端 /finance/ledger/personal/import/csv，上限 5MB）：
// 坏行由后端逐行跳过并返回 errors，好行照常入库
const importing = ref(false)
const csvInput = ref(null)

function pickCsv() {
  csvInput.value?.click()
}

async function onCsvPicked(e) {
  const file = e.target.files?.[0]
  e.target.value = '' // 允许重复选择同一文件
  if (!file) return
  importing.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const { data } = await api.post('/finance/ledger/personal/import/csv', fd, { timeout: 120000 })
    const errors = data.errors || []
    ElMessage.success(`导入完成：新增 ${data.imported ?? 0} 行` + (errors.length ? `，${errors.length} 行未导入` : ''))
    if (errors.length) {
      ElMessageBox.alert(errors.slice(0, 10).join('\n'), '部分行未导入（最多显示 10 条）', { confirmButtonText: '知道了' })
    }
    await reload(1)
  } catch {
    /* 拦截器已统一提示（B6：业务 catch 不重复弹） */
  } finally {
    importing.value = false
  }
}

onMounted(async () => {
  await load()
  await reload()
})
</script>

<style scoped>
.total-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
  margin-left: 8px;
}

.total-hint.warn {
  color: var(--el-color-warning);
}

/* CSV 导入的隐藏文件选择器：由「导入 CSV」按钮代理点击 */
.hidden-file {
  display: none;
}

.filters {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
</style>
