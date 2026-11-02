<template>
  <div class="page" v-loading="loading">
    <template v-if="!failed">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>批次详情 #{{ batch.id }}（{{ batch.file_count }} 个文件）</span>
          <div>
            <el-button size="small" :loading="reclassifying" :disabled="!batch.status || batch.status === 'uploaded'" @click="doReclassify">
              重新归类
            </el-button>
            <el-button size="small" :disabled="!batch.status || batch.status === 'uploaded'" :loading="exporting" @click="doExport">
              导出（重命名 + 清单 + 拆分 ZIP）
            </el-button>
            <el-button v-if="batch.status === 'exported'" size="small" type="primary" @click="download">下载整批 ZIP</el-button>
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
          <span>项目拆分（{{ projects.length }} 个项目）</span>
          <span class="hint">{{ zipHint }}</span>
        </div>
      </template>
      <el-table :data="projects" size="small" border empty-text="暂无有效发票明细">
        <el-table-column label="项目" min-width="160">
          <template #default="{ row }">
            <el-tag v-if="row.project === UNCLASSIFIED" type="warning" size="small">未归类</el-tag>
            <span v-else>{{ row.project }}</span>
            <el-tag v-if="row.manual" size="small" type="info" style="margin-left: 6px">人工修正 {{ row.manual }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="张数" width="90" align="right" />
        <el-table-column label="净额" width="150" align="right">
          <template #default="{ row }">{{ fmtAmount(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="项目 ZIP" min-width="220">
          <template #default="{ row }">
            <el-button v-if="row.zip_ready" size="small" link type="primary" @click="downloadProject(row.project, row.zip_file)">
              下载 {{ row.zip_file }}
            </el-button>
            <span v-else class="hint">未导出</span>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="hasUnclassified" class="hint warn">
        存在「未归类」发票：在下方明细表逐张修改「项目」后重新导出即可并入对应项目。
      </p>
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
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'
import { isRedInvoice } from '../itemDisplay'

const UNCLASSIFIED = '未归类'
const route = useRoute()
const loading = ref(false)
const exporting = ref(false)
const reclassifying = ref(false)
const failed = ref(false)
const keyword = ref('')
const batch = ref({ items: [] })
const projects = ref([])

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

const hasUnclassified = computed(() => projects.value.some((row) => row.project === UNCLASSIFIED && row.count > 0))
const zipHint = computed(() =>
  batch.value.status === 'exported' ? '每个项目一个 ZIP，可单独下载' : '导出后按项目拆分打包，可单独下载',
)

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
    await loadProjects()
  } catch {
    // 加载失败：切到错误态，不再渲染详情壳（否则标题显示「批次详情 #undefined」）
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function loadProjects() {
  try {
    const { data } = await api.get(`/finance/invoice/batches/${route.params.id}/projects`)
    projects.value = data || []
  } catch {
    projects.value = []
  }
}

async function patchItem(row) {
  try {
    await api.patch(`/finance/invoice/items/${row.id}`, {
      project_name: row.project_name || null,
      remark: row.remark || null,
    })
    await loadProjects()
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function doReclassify() {
  if (batch.value.status === 'exported') {
    try {
      await ElMessageBox.confirm(
        '重新归类会作废当前导出包（需重新导出后才能下载 ZIP），人工修正过的发票不会被覆盖。继续？',
        '重新归类',
        { type: 'warning', confirmButtonText: '继续', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  reclassifying.value = true
  try {
    // 未归类明细需补取 PDF 全文，耗时随待判定张数增长
    const { data } = await api.post(`/finance/invoice/batches/${batch.value.id}/reclassify`, {}, { timeout: 180000 })
    ElMessage.success(`重新归类完成：变更 ${data.changed} 张，未归类 ${data.unclassified} 张`)
    await load()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    reclassifying.value = false
  }
}

async function doExport() {
  exporting.value = true
  try {
    const { data } = await api.post(`/finance/invoice/batches/${batch.value.id}/export`)
    const zips = Object.keys(data.project_zips || {}).length
    ElMessage.success(`导出完成：${data.projects} 个项目，拆分 ZIP ${zips} 个，${(data.zip_size / 1024).toFixed(1)} KB`)
    await load()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    exporting.value = false
  }
}

function download() {
  // 整批下载文件名由后端按「发票汇总+月份」给出（download_name），前端显式落名
  saveZip(
    `/finance/invoice/batches/${batch.value.id}/download`,
    batch.value.download_name || `发票汇总批次${batch.value.id}.zip`,
  )
}

function downloadProject(project, zipFile) {
  // 「项目+月份.zip」：文件名由后端导出时生成（清单 returns zip_file），前端显式落名
  saveZip(
    `/finance/invoice/batches/${batch.value.id}/projects/download?project=${encodeURIComponent(project)}`,
    zipFile || '项目发票.zip',
  )
}

// 以 Blob + a.download 下载：不依赖浏览器解析 Content-Disposition，
// 避免中文文件名被丢成随机 UUID（用户实测下载记录里出现无扩展名的乱码名）。
async function saveZip(url, filename) {
  try {
    const { data } = await api.get(url, { responseType: 'blob', timeout: 300000 })
    const blobUrl = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    // 稍后回收对象 URL，避免过早 revoke 打断下载
    setTimeout(() => URL.revokeObjectURL(blobUrl), 10000)
  } catch {
    /* 拦截器已统一提示 */
  }
}

onMounted(load)
</script>

<style scoped>
.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.hint.warn {
  color: var(--el-color-warning);
  margin: 8px 0 0;
}
</style>
