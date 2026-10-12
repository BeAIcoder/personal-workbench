<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>凭证查询</span>
          <el-button size="small" @click="$router.push({ name: 'finance-ledger-bip' })">去导入</el-button>
        </div>
      </template>
      <div class="filters">
        <el-input v-model="filters.period" placeholder="期间 YYYY-MM" size="small" style="width: 140px" clearable />
        <el-input v-model="filters.ledger" placeholder="核算账簿" size="small" style="width: 140px" clearable />
        <el-input v-model="filters.voucher_no" placeholder="凭证号" size="small" style="width: 130px" clearable />
        <el-input v-model="filters.keyword" placeholder="摘要关键词" size="small" style="width: 150px" clearable @keyup.enter="reload" />
        <el-button size="small" type="primary" @click="reload">筛选</el-button>
        <el-button size="small" @click="exportToVault" :disabled="!filters.period">按期间入仓</el-button>
      </div>
      <el-table :data="items" size="small" border empty-text="暂无凭证">
        <el-table-column prop="voucher_date" label="日期" width="110" />
        <el-table-column prop="ledger" label="核算账簿" min-width="120" show-overflow-tooltip />
        <el-table-column prop="voucher_no" label="凭证号" width="110" />
        <el-table-column prop="summary" label="摘要" min-width="160" show-overflow-tooltip />
        <el-table-column prop="entry_count" label="分录" width="70" align="right" />
        <el-table-column label="借方合计" width="120" align="right">
          <template #default="{ row }">{{ row.debit_total?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="贷方合计" width="120" align="right">
          <template #default="{ row }">{{ row.credit_total?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="平衡" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="row.balanced ? 'success' : 'danger'" size="small">{{ row.balanced ? '平' : '不平' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="showDetail(row.id)">分录</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        small
        style="margin-top: 12px; justify-content: flex-end"
        @current-change="reload"
      />
    </el-card>

    <el-drawer v-model="detailVisible" :title="`凭证分录（${detail.voucher_no}）`" size="480px">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="日期">{{ detail.voucher_date }}</el-descriptions-item>
        <el-descriptions-item label="账簿">{{ detail.ledger }}</el-descriptions-item>
        <el-descriptions-item label="摘要" :span="2">{{ detail.summary }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="detail.entries" size="small" border style="margin-top: 12px">
        <el-table-column prop="summary" label="摘要" min-width="120" show-overflow-tooltip />
        <el-table-column prop="subject_code" label="科目" width="90" />
        <el-table-column prop="subject_name" label="科目名称" min-width="110" show-overflow-tooltip />
        <el-table-column label="借方" width="100" align="right">
          <template #default="{ row }">{{ row.debit ? row.debit.toFixed(2) : '' }}</template>
        </el-table-column>
        <el-table-column label="贷方" width="100" align="right">
          <template #default="{ row }">{{ row.credit ? row.credit.toFixed(2) : '' }}</template>
        </el-table-column>
      </el-table>
      <div class="drawer-total">
        借方合计 {{ detail.debit_total?.toFixed(2) }} ｜ 贷方合计 {{ detail.credit_total?.toFixed(2) }}
        <el-tag :type="detail.balanced ? 'success' : 'danger'" size="small">{{ detail.balanced ? '平衡' : '不平衡' }}</el-tag>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filters = reactive({ period: '', ledger: '', voucher_no: '', keyword: '' })

const detailVisible = ref(false)
const detail = reactive({ voucher_no: '', entries: [] })

async function reload() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
    for (const k of Object.keys(filters)) {
      if (filters[k]) params[k] = filters[k]
    }
    const { data } = await api.get('/finance/ledger/bip/vouchers', { params })
    items.value = data.items
    total.value = data.total
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

async function showDetail(id) {
  try {
    const { data } = await api.get(`/finance/ledger/bip/vouchers/${id}`)
    Object.assign(detail, data)
    detailVisible.value = true
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  }
}

async function exportToVault() {
  try {
    const { data } = await api.post('/finance/ledger/bip/vouchers/export-to-vault', {
      period: filters.period,
    })
    ElMessage.success(`已入仓：${data.table}（${data.rows} 行）`)
  } catch (e) {
    const code = e?.response?.status
    ElMessage.error(code === 503 ? '数据仓模块当前不可用' : (e?.response?.data?.detail || '入仓失败'))
  }
}

onMounted(reload)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.drawer-total {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  color: #606266;
}
</style>
