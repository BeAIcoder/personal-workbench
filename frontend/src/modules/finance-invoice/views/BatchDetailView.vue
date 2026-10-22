<template>
  <div class="page" v-loading="loading">
    <template v-if="!failed">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>批次详情 #{{ batch.id }}（{{ batch.file_count }} 个文件）</span>
          <div>
            <el-button size="small" :disabled="!batch.status || batch.status === 'uploaded'" :loading="exporting" @click="doExport">
              导出（重命名 + 清单 + ZIP）
            </el-button>
            <el-button v-if="batch.status === 'exported'" size="small" type="primary" @click="download">下载 ZIP</el-button>
            <el-button size="small" @click="$router.back()">返回</el-button>
          </div>
        </div>
      </template>
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="状态">
          <el-tag :type="statusType(batch.status)" size="small">{{ statusText(batch.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="已解析">{{ batch.parsed_count }}</el-descriptions-item>
        <el-descriptions-item label="失败">{{ batch.failed_count }}</el-descriptions-item>
        <el-descriptions-item label="重复">{{ batch.duplicate_count }}</el-descriptions-item>
        <el-descriptions-item label="红冲已配对">{{ batch.red_matched_count }}</el-descriptions-item>
        <el-descriptions-item label="红冲未配对">{{ batch.red_unmatched_count }}</el-descriptions-item>
        <el-descriptions-item label="金额合计">{{ fmtAmount(batch.total_amount) }}</el-descriptions-item>
        <el-descriptions-item label="错误">{{ batch.error || '—' }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div class="card-head">
          <span>发票明细（{{ batch.items?.length || 0 }}）</span>
          <el-input v-model="keyword" placeholder="发票号 / 销方 / 购方 过滤" size="small" style="width: 240px" clearable />
        </div>
      </template>
      <el-table :data="filteredItems" size="small" border max-height="600">
        <el-table-column prop="invoice_number" label="发票号" min-width="140" />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            {{ row.invoice_type }}<el-tag v-if="isRedInvoice(row)" type="danger" size="small" style="margin-left: 4px">红</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="invoice_date" label="开票日期" width="110" />
        <el-table-column prop="seller_name" label="销方" min-width="160" show-overflow-tooltip />
        <el-table-column prop="amount" label="金额" width="110" align="right" />
        <el-table-column prop="tax_amount" label="税额" width="100" align="right" />
        <el-table-column label="项目" min-width="120">
          <template #default="{ row }">
            <el-input v-model="row.project_name" size="small" @blur="patchItem(row)" placeholder="未归类" />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.extraction_success ? 'success' : 'danger'" size="small">
              {{ row.extraction_success ? (row.corrected ? '已修正' : '已解析') : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="120">
          <template #default="{ row }">
            <el-input v-model="row.remark" size="small" @blur="patchItem(row)" />
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    </template>
    <el-result v-else icon="error" title="批次不存在或加载失败" sub-title="请返回批次列表查看最新批次" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../api'
import { isRedInvoice } from '../itemDisplay'

const route = useRoute()
const loading = ref(false)
const exporting = ref(false)
const failed = ref(false)
const keyword = ref('')
const batch = ref({ items: [] })

// 金额合计可能为 null（解析未完成/失败批次）：直接 toLocaleString 会得到空白，
// 统一用「—」占位；合法值保持既有千分位 + 两位小数口径
function fmtAmount(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—'
}

const STATUS_TEXT = { uploaded: '待解析', parsed: '已解析', exported: '已导出', failed: '失败' }
const STATUS_TYPE = { uploaded: 'info', parsed: 'success', exported: 'primary', failed: 'danger' }
const statusText = (s) => STATUS_TEXT[s] || s
const statusType = (s) => STATUS_TYPE[s] || 'info'

const filteredItems = computed(() => {
  const kw = keyword.value.trim()
  const items = batch.value.items || []
  if (!kw) return items
  return items.filter((i) =>
    [i.invoice_number, i.seller_name, i.buyer_name].some((v) => (v || '').includes(kw)),
  )
})

async function load() {
  loading.value = true
  failed.value = false
  try {
    const { data } = await api.get(`/finance/invoice/batches/${route.params.id}`)
    batch.value = data
  } catch {
    // 加载失败：切到错误态，不再渲染详情壳（否则标题显示「批次详情 #undefined」）
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function patchItem(row) {
  try {
    await api.patch(`/finance/invoice/items/${row.id}`, {
      project_name: row.project_name || null,
      remark: row.remark || null,
    })
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function doExport() {
  exporting.value = true
  try {
    const { data } = await api.post(`/finance/invoice/batches/${batch.value.id}/export`)
    ElMessage.success(`导出完成：${data.projects} 个项目，${(data.zip_size / 1024).toFixed(1)} KB`)
    await load()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    exporting.value = false
  }
}

function download() {
  window.open(`/api/finance/invoice/batches/${batch.value.id}/download`, '_blank')
}

onMounted(load)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>
