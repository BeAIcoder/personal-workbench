<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>发票批次</span>
          <div>
            <el-tag v-if="engineStatus && !engineStatus.available" type="warning" style="margin-right: 8px">
              解析引擎不可用
            </el-tag>
            <el-button size="small" type="primary" @click="uploadDialog = true">上传发票</el-button>
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
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="goDetail(row.id)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
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
      <p class="hint">仅支持文本型数电发票 PDF；扫描件无法解析。上传后自动建批次并后台解析。</p>
      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!form.files.length" @click="submitUpload">上传并解析</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const router = useRouter()
const loading = ref(false)
const items = ref([])
const engineStatus = ref(null)

const uploadDialog = ref(false)
const submitting = ref(false)
const form = reactive({ name: '', files: [] })

const STATUS_TEXT = { uploaded: '待解析', parsed: '已解析', exported: '已导出', failed: '失败' }
const STATUS_TYPE = { uploaded: 'info', parsed: 'success', exported: 'primary', failed: 'danger' }
const statusText = (s) => STATUS_TEXT[s] || s
const statusType = (s) => STATUS_TYPE[s] || 'info'

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/invoice/batches')
    items.value = data || []
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function onFiles(e) {
  form.files = Array.from(e.target.files || [])
}

async function submitUpload() {
  if (!form.files.length) return
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('name', form.name)
    for (const f of form.files) fd.append('files', f)
    await api.post('/finance/invoice/batches', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
    ElMessage.success('批次已创建，解析完成')
    uploadDialog.value = false
    form.name = ''
    form.files = []
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '上传失败')
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
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hint {
  color: #909399;
  font-size: 13px;
}
</style>
