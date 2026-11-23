<template>
  <div class="page" v-loading="loading">
    <HelpLink section="6.3" />
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>发票批次</span>
          <div>
            <el-tag v-if="engineStatus && !engineStatus.available" type="warning" style="margin-right: 8px">
              解析引擎不可用
            </el-tag>
            <el-button size="small" type="primary" @click="uploadDialog = true">上传发票</el-button>
            <AskAiButton :prompt="aiPrompt" />
          </div>
        </div>
      </template>
      <el-table :data="items" size="small" empty-text="暂无批次，点右上角上传发票 PDF / ZIP">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="name" label="批次名称" min-width="160" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="file_count" label="文件数" width="80" align="right" />
        <el-table-column prop="parsed_count" label="已解析" width="80" align="right" />
        <el-table-column prop="failed_count" label="失败" width="80" align="right" />
        <el-table-column label="金额合计" width="130" align="right">
          <template #default="{ row }">{{ row.total_amount?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="goDetail(row.id)">详情</el-button>
            <el-button size="small" link type="danger" @click="removeBatch(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        class="pager" background layout="total, sizes, prev, pager, next"
        :total="total" :page-size="pageSize" :current-page="page"
        :page-sizes="[50, 100, 200]"
        @current-change="onPageChange" @size-change="onSizeChange"
      />
    </el-card>

    <el-dialog v-model="uploadDialog" title="上传发票（PDF / ZIP）" width="480px">
      <el-form label-width="80px">
        <el-form-item label="批次名称">
          <el-input v-model="form.name" placeholder="可空，默认按时间命名" />
        </el-form-item>
        <el-form-item label="文件">
          <input type="file" multiple accept=".pdf,.zip" @change="onFiles" />
        </el-form-item>
      </el-form>
      <p class="hint">仅支持文本型数电发票 PDF；扫描件无法解析。上传后在本次请求内同步解析，几十张以上可能需要 1-2 分钟，请勿重复提交。</p>
      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!form.files.length" @click="submitUpload">上传并解析</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import HelpLink from '../../../components/HelpLink.vue'
import api from '../../../api'
import { takeFiles } from '../../_shared/fileInput'
import AskAiButton from '../../../components/assistant/AskAiButton.vue'
import { nextPageAfterDelete, pageState, toPageParams } from '../../_shared/pagedList.js'

const router = useRouter()
const loading = ref(false)
const items = ref([])
const engineStatus = ref(null)
const page = ref(1)
// 首屏保持改造前的 50 条（旧后端 limit 默认 50），不是 20
const pageSize = ref(50)
const total = ref(0)

const uploadDialog = ref(false)
const submitting = ref(false)
const form = reactive({ name: '', files: [] })

const aiPrompt = computed(() => '请分析发票批次列表：跨批次重复发票、冲红配对情况与金额异常')

const STATUS_TEXT = { uploaded: '待解析', parsed: '已解析', exported: '已导出', failed: '失败' }
const STATUS_TYPE = { uploaded: 'info', parsed: 'success', exported: 'primary', failed: 'danger' }
const statusText = (s) => STATUS_TEXT[s] || s
const statusType = (s) => STATUS_TYPE[s] || 'info'

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/invoice/batches', {
      params: toPageParams(null, page.value, pageSize.value),
    })
    const state = pageState(data, { page: page.value, pageSize: pageSize.value })
    items.value = state.items
    total.value = state.total
    page.value = state.page
    pageSize.value = state.pageSize
  } catch {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function onPageChange(next) {
  page.value = next
  return load()
}

function onSizeChange(size) {
  pageSize.value = size
  page.value = 1
  return load()
}

async function removeBatch(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除批次「${row.name || '#' + row.id}」？将同时删除全部发票明细、上传留档与已导出 ZIP，且不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const targetPage = nextPageAfterDelete(page.value, total.value, pageSize.value)
  try {
    // 删除要清明细、inbox 留档与导出 ZIP，文件多时 10s 不够
    await api.delete(`/finance/invoice/batches/${row.id}`, { timeout: 120000 })
    page.value = targetPage
    await load()
  } catch {
    /* 拦截器已统一提示 */
  }
}

function onFiles(e) {
  form.files = takeFiles(e)
}

async function submitUpload() {
  if (!form.files.length) return
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('name', form.name)
    for (const f of form.files) fd.append('files', f)
    // 后端在请求内同步解析 ZIP/PDF，全局 10s 超时会误报失败并诱发重复批次
    await api.post('/finance/invoice/batches', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    ElMessage.success('批次已创建，解析完成')
    uploadDialog.value = false
    form.name = ''
    form.files = []
    page.value = 1 // 新批次 id 最大，回第一页才看得到
    await load()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    submitting.value = false
  }
}

function goDetail(id) {
  router.push({ name: 'finance-invoice-detail', params: { id } })
}

onMounted(async () => {
  try {
    const { data } = await api.get('/finance/invoice/engine/status')
    engineStatus.value = data
  } catch { /* 状态自检失败不阻塞列表 */ }
  await load()
})
</script>

<style scoped>
.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.pager { margin-top: 10px; justify-content: flex-end; }
</style>
