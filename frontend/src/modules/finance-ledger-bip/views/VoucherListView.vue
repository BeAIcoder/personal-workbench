<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>凭证查询</span>
          <el-button
            v-for="entry in bipPageEntries"
            :key="entry.name"
            size="small"
            @click="$router.push({ name: entry.name })"
          >
            {{ entry.title }}
          </el-button>
          <el-button size="small" @click="$router.push({ name: 'finance-ledger-bip' })">去导入</el-button>
        </div>
      </template>
      <div class="filters">
        <el-input v-model="filters.period" placeholder="期间 YYYY-MM" size="small" style="width: 140px" clearable />
        <el-input v-model="filters.ledger" placeholder="核算账簿" size="small" style="width: 140px" clearable />
        <el-input v-model="filters.voucher_no" placeholder="凭证号" size="small" style="width: 130px" clearable />
        <el-input v-model="filters.keyword" placeholder="摘要关键词" size="small" style="width: 150px" clearable @keyup.enter="search" />
        <el-button size="small" type="primary" @click="search">筛选</el-button>
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

<script>
// 科目汇总页已有路由但侧栏不可见，在凭证查询页提供可见入口（审计 F04）。
// 入口按路由 name 跳转；name 由模块 manifest 注册，测试校验均指向已注册路由。
export const BIP_PAGE_ENTRIES = [{ name: 'finance-ledger-bip-summary', title: '科目汇总' }]
</script>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'
import { applyFilters } from '../../_shared/filters'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filters = reactive({ period: '', ledger: '', voucher_no: '', keyword: '' })
const bipPageEntries = BIP_PAGE_ENTRIES

const detailVisible = ref(false)
const detail = reactive({ voucher_no: '', entries: [] })

// 请求序号：连续打开不同凭证的分录抽屉时丢弃过期响应，避免旧详情覆盖新详情
let detailSeq = 0
// 列表请求序号：快速翻页/筛选时丢弃慢的旧页，避免旧数据覆盖新页
let loadSeq = 0

async function reload() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
    for (const k of Object.keys(filters)) {
      if (filters[k]) params[k] = filters[k]
    }
    const { data } = await api.get('/finance/ledger/bip/vouchers', { params })
    if (seq !== loadSeq) return
    items.value = data.items
    total.value = data.total
  } catch {
    if (seq !== loadSeq) return
    items.value = []
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function search() {
  applyFilters(page, reload)
}

async function showDetail(id) {
  const seq = ++detailSeq
  try {
    const { data } = await api.get(`/finance/ledger/bip/vouchers/${id}`)
    if (seq !== detailSeq) return  // 已有更新的请求发出，丢弃旧结果
    Object.assign(detail, data)
    detailVisible.value = true
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function exportToVault() {
  try {
    const { data } = await api.post('/finance/ledger/bip/vouchers/export-to-vault', {
      period: filters.period,
    })
    ElMessage.success(`已入仓：${data.table}（${data.rows} 行）`)
  } catch {
    /* 拦截器已统一提示 */
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
