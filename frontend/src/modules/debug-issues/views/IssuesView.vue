<template>
  <div class="page" v-loading="loading">
    <!-- 汇总卡片 -->
    <el-row v-if="summary" :gutter="12" class="summary-row">
      <el-col v-for="sev in ['P0', 'P1', 'P2', 'P3']" :key="sev" :span="3">
        <el-card shadow="never" class="sum-card">
          <div class="sum-num">
            <el-tag size="small" :style="sevTagStyle(sev)">{{ sev }}</el-tag>
            {{ summary.open_issues_by_severity?.[sev] ?? 0 }}
          </div>
          <div class="sum-label">未决问题</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="sum-card">
          <div class="sum-num">
            <template v-if="summary.last_run">
              <el-tag :type="lastRunOk ? 'success' : 'danger'" size="small">
                {{ lastRunOk ? '通过' : '有失败' }}
              </el-tag>
              {{ kindLabel(summary.last_run.kind) }}
            </template>
            <span v-else class="sum-none">暂无运行</span>
          </div>
          <div class="sum-label">
            最近测试运行<template v-if="summary.last_run">（{{ summary.last_run.finished_at }}）</template>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="sum-card">
          <div class="sum-num">{{ summary.client_errors_count }}</div>
          <div class="sum-label">前端报错（累计）</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>问题记录（{{ total }}）</span>
          <div class="head-ops">
            <el-select v-model="filterStatus" placeholder="状态" size="small" clearable style="width: 130px" @change="loadIssues">
              <el-option v-for="s in STATUSES" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
            <el-select v-model="filterSeverity" placeholder="级别" size="small" clearable style="width: 110px" @change="loadIssues">
              <el-option v-for="s in ['P0', 'P1', 'P2', 'P3']" :key="s" :label="s" :value="s" />
            </el-select>
            <el-button type="primary" size="small" @click="openDialog()">新建问题</el-button>
          </div>
        </div>
      </template>
      <el-table :data="items" size="small">
        <el-table-column label="级别" width="70">
          <template #default="{ row }">
            <el-tag size="small" :style="sevTagStyle(row.severity)">{{ row.severity }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="260" show-overflow-tooltip />
        <el-table-column prop="page" label="页面" width="130" show-overflow-tooltip />
        <el-table-column label="来源" width="90">
          <template #default="{ row }">{{ sourceLabel(row.source) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-select :model-value="row.status" size="small" @change="(v) => changeStatus(row, v)">
              <el-option v-for="s in STATUSES" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="160" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openDialog(row)">编辑</el-button>
            <el-button size="small" link type="danger" @click="removeIssue(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建 / 编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑问题' : '新建问题'" width="560px">
      <el-form label-width="70px" size="default">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" maxlength="200" placeholder="一句话说清问题" />
        </el-form-item>
        <el-form-item label="级别">
          <el-radio-group v-model="form.severity">
            <el-radio-button v-for="s in ['P0', 'P1', 'P2', 'P3']" :key="s" :value="s">{{ s }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width: 180px">
            <el-option v-for="s in STATUSES" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <el-select v-model="form.source" style="width: 180px">
            <el-option label="手工记录" value="manual" />
            <el-option label="E2E 测试" value="e2e" />
            <el-option label="代码分析" value="analysis" />
          </el-select>
        </el-form-item>
        <el-form-item label="页面">
          <el-input v-model="form.page" maxlength="200" placeholder="如 /tasks、菜单" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="4" maxlength="4000" placeholder="现象、定位（文件:行号）、影响" />
        </el-form-item>
        <el-form-item label="证据">
          <el-input v-model="form.evidence" maxlength="200" placeholder="关联的测试运行 id（可为空）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { debugApi } from '../../../api'

const STATUSES = [
  { value: 'open', label: '未处理' },
  { value: 'in_progress', label: '处理中' },
  { value: 'done', label: '已完成' },
  { value: 'wontfix', label: '不修复' },
]

const loading = ref(false)
const saving = ref(false)
const items = ref([])
const total = ref(0)
const summary = ref(null)
const filterStatus = ref('')
const filterSeverity = ref('')
const dialogVisible = ref(false)
const form = reactive({ id: '', title: '', severity: 'P2', status: 'open', source: 'manual', page: '', description: '', evidence: '' })

const lastRunOk = computed(() => {
  const r = summary.value?.last_run
  return r ? r.bad_pages === 0 && r.failed_tests === 0 : true
})

// 与运行列表页一致的中文类型名
function kindLabel(kind) {
  return { recon: '页面侦察', interactions: '交互测试', edge: '边界测试' }[kind] || kind
}

// P0 红 / P1 橙 / P2 黄 / P3 灰
function sevTagStyle(sev) {
  if (sev === 'P0') return { background: '#f56c6c', borderColor: '#f56c6c', color: '#fff' }
  if (sev === 'P1') return { background: '#e6a23c', borderColor: '#e6a23c', color: '#fff' }
  if (sev === 'P2') return { background: '#f7c948', borderColor: '#f7c948', color: '#5f4b00' }
  return { background: '#909399', borderColor: '#909399', color: '#fff' }
}

function sourceLabel(source) {
  return { manual: '手工', e2e: 'E2E', analysis: '分析' }[source] || source
}

async function loadSummary() {
  try {
    const { data } = await debugApi.summary()
    summary.value = data
  } catch {
    summary.value = null
  }
}

async function loadIssues() {
  loading.value = true
  try {
    const params = {}
    if (filterStatus.value) params.status = filterStatus.value
    if (filterSeverity.value) params.severity = filterSeverity.value
    const { data } = await debugApi.issues.list(params)
    items.value = data.items || []
    total.value = data.total ?? items.value.length
  } catch {
    items.value = []
    total.value = 0  // 列表清空时计数同步归零，否则表头停在旧数字（「问题记录（11）」配空表）
  } finally {
    loading.value = false
  }
}

function openDialog(row) {
  Object.assign(form, row
    ? { id: row.id, title: row.title, severity: row.severity, status: row.status, source: row.source, page: row.page, description: row.description, evidence: row.evidence }
    : { id: '', title: '', severity: 'P2', status: 'open', source: 'manual', page: '', description: '', evidence: '' })
  dialogVisible.value = true
}

async function save() {
  if (!form.title.trim()) {
    ElMessage.warning('请填写标题')
    return
  }
  saving.value = true
  try {
    const payload = { title: form.title.trim(), severity: form.severity, status: form.status, source: form.source, page: form.page, description: form.description, evidence: form.evidence }
    if (form.id) {
      await debugApi.issues.update(form.id, payload)
      ElMessage.success('已更新')
    } else {
      await debugApi.issues.create(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await Promise.all([loadIssues(), loadSummary()])
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

async function changeStatus(row, status) {
  try {
    await debugApi.issues.update(row.id, { status })
    row.status = status
    ElMessage.success('状态已更新')
    // 状态筛选生效时，改状态后该行可能不再属于当前筛选，必须重新拉取列表
    await Promise.all([loadIssues(), loadSummary()])
  } catch {
    /* 拦截器已提示 */
  }
}

async function removeIssue(row) {
  try {
    await ElMessageBox.confirm(`确定删除问题「${row.title}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await debugApi.issues.remove(row.id)
    ElMessage.success('已删除')
    await Promise.all([loadIssues(), loadSummary()])
  } catch {
    /* 拦截器已提示 */
  }
}

onMounted(() => {
  loadIssues()
  loadSummary()
})
</script>

<style scoped>
.summary-row {
  margin-bottom: 16px;
}

.sum-card :deep(.el-card__body) {
  padding: 12px 16px;
}

.sum-num {
  font-size: 20px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}

.sum-none {
  color: #909399;
  font-size: 14px;
  font-weight: 400;
}

.sum-label {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.head-ops {
  display: flex;
  gap: 8px;
}
</style>
