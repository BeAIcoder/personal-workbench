<template>
  <div class="panel">
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>认领 ↔ 核销匹配复核</span>
          <span class="head-actions">
            <el-button size="small" :loading="matchesLoading" :disabled="!batch" @click="loadMatches">刷新</el-button>
            <el-button size="small" type="primary" :disabled="!batch" @click="openMatchDialog">人工新增匹配</el-button>
          </span>
        </div>
      </template>
      <p v-if="!batch" class="empty-hint">尚未选择批次。</p>
      <el-table v-else :data="matches" size="small" empty-text="暂无匹配建议：机检只在勾稽检查后给出认领↔核销候选，未匹配的认领保持未匹配">
        <el-table-column label="认领侧" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ matchSideText(row.claim) }}</template>
        </el-table-column>
        <el-table-column label="核销侧" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ matchSideText(row.writeoff) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="matchStatusMeta(row.status).type">{{ matchStatusMeta(row.status).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="origin" label="来源" width="70">
          <template #default="{ row }">{{ originLabel(row.origin) }}</template>
        </el-table-column>
        <el-table-column label="分摊金额" width="160">
          <template #default="{ row }">
            <el-input-number
              v-if="row.status === 'suggested'"
              :model-value="allocDrafts[row.id]"
              :precision="2"
              :min="0.01"
              :controls="false"
              size="small"
              class="alloc-input"
              @update:model-value="(v) => setAllocation(row, v)"
            />
            <span v-else>{{ row.allocated_amount ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="说明" min-width="160" show-overflow-tooltip />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'suggested'">
              <el-button size="small" link type="primary" @click="confirmMatch(row)">确认</el-button>
              <el-button size="small" link type="danger" @click="rejectMatch(row)">拒绝</el-button>
            </template>
            <span v-else class="muted">已裁定</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>问题清单（机检 + 人工；确认/排除需填写说明，机检重跑不会覆盖人工裁定）</span>
          <span class="head-actions">
            <el-button size="small" :loading="issuesLoading" :disabled="!batch" @click="loadIssues">刷新</el-button>
            <el-button size="small" type="primary" :disabled="!batch" @click="openIssueDialog">人工补记问题</el-button>
          </span>
        </div>
      </template>
      <el-alert
        v-if="batch && pendingCount > 0"
        :title="`当前存在 ${pendingCount} 项待确认问题/差异：导出报告前请逐项确认或排除，未决事项会在报告中显著标注。`"
        type="warning"
        :closable="false"
        class="gap-bottom"
      />
      <el-table v-if="batch" :data="issues" size="small" empty-text="暂无问题记录">
        <el-table-column label="来源" width="70">
          <template #default="{ row }">{{ originLabel(row.origin) }}</template>
        </el-table-column>
        <el-table-column prop="category_label" label="类别" min-width="150" show-overflow-tooltip />
        <el-table-column label="级别" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="severityMeta(row.severity).type">{{ severityMeta(row.severity).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="issueStatusMeta(row.status).type">{{ issueStatusMeta(row.status).label }}</el-tag>
            <el-tag v-if="!row.active" size="small" type="info" class="gap-left-sm">非活动</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="amount_impact" label="金额影响" width="100" align="right">
          <template #default="{ row }">{{ row.amount_impact ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="证据" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.evidence || []).join('；') || '—' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="说明" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.note || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="askResolve(row, 'confirmed')">确认</el-button>
            <el-button size="small" link type="info" @click="askResolve(row, 'dismissed')">排除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="block">
      <template #header><span>导出（xlsx 底稿 / Markdown 报告 / Word 报告）</span></template>
      <el-alert
        v-if="batch && batch.needs_recheck"
        title="口径参数在上次勾稽检查通过后又被修改：请重新执行勾稽检查后再导出（此时导出会被拒绝）。"
        type="warning"
        :closable="false"
        class="gap-bottom"
      />
      <el-alert
        v-else-if="batch && pendingCount > 0"
        :title="`存在 ${pendingCount} 项待确认问题：报告将显著标注未决事项（草稿提示），可继续导出但请尽快复核。`"
        type="info"
        :closable="false"
        class="gap-bottom"
      />
      <span class="head-actions">
        <el-button :loading="exportBusy === 'xlsx'" :disabled="!batch || !!exportBusy" @click="download('xlsx')">下载底稿 xlsx</el-button>
        <el-button :loading="exportBusy === 'md'" :disabled="!batch || !!exportBusy" @click="download('md')">下载报告 Markdown</el-button>
        <el-button :loading="exportBusy === 'docx'" :disabled="!batch || !!exportBusy" @click="download('docx')">下载报告 Word</el-button>
      </span>
      <p class="muted">三种格式由同一报告视图生成：合计、差异与未决事项一致；不含完整账号等敏感字段。</p>
    </el-card>

    <el-dialog v-model="matchDialog" title="人工新增认领 ↔ 核销匹配" width="560px">
      <el-form label-width="110px" size="small">
        <el-form-item label="认领记录">
          <el-select v-model="matchForm.claim_record_id" filterable placeholder="选择认领记录" class="full-width">
            <el-option v-for="o in claimOptions" :key="o.value" :value="o.value" :label="o.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="核销记录">
          <el-select v-model="matchForm.writeoff_record_id" filterable placeholder="选择核销记录（限本批合同范围）" class="full-width">
            <el-option v-for="o in writeoffOptions" :key="o.value" :value="o.value" :label="o.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="分摊金额">
          <el-input-number v-model="matchForm.allocated_amount" :precision="2" :min="0.01" :controls="false" class="alloc-input" />
          <span class="muted">部分分摊请填实际认可金额；确认时校验双方累计不超额</span>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="matchForm.note" type="textarea" :rows="2" placeholder="人工判断依据（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="matchDialog = false">取消</el-button>
        <el-button type="primary" :loading="matchCreating" @click="createMatch">创建建议</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="issueDialog" title="人工补记问题 / 待补资料" width="520px">
      <el-form label-width="90px" size="small">
        <el-form-item label="类别">
          <el-input v-model="issueForm.category" placeholder="如 保证金重复核销 / 缺 X 月欠费明细" />
        </el-form-item>
        <el-form-item label="级别">
          <el-radio-group v-model="issueForm.severity">
            <el-radio value="info">提示</el-radio>
            <el-radio value="warn">警告</el-radio>
            <el-radio value="error">严重</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="金额影响">
          <el-input-number v-model="issueForm.amount_impact" :precision="2" :controls="false" class="alloc-input" placeholder="可空" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="issueForm.note" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="issueDialog = false">取消</el-button>
        <el-button type="primary" :loading="issueCreating" @click="createIssue">登记</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resolveDialog" :title="resolveTitle" width="480px">
      <el-input v-model="resolveNote" type="textarea" :rows="3" placeholder="裁定说明（必填）：为什么确认或排除" />
      <template #footer>
        <el-button @click="resolveDialog = false">取消</el-button>
        <el-button type="primary" :loading="resolveSaving" @click="submitResolve">提交裁定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  exportFileName,
  issueStatusMeta,
  matchStatusMeta,
  originLabel,
  reconApi,
  saveBlob,
  severityMeta,
} from '../api'

const props = defineProps({ batch: { type: Object, default: null } })
const emit = defineEmits(['refresh'])

function matchSideText(side) {
  if (!side) return '—'
  const parts = [`#${side.record_id}`]
  if (side.doc_no) parts.push(side.doc_no)
  if (side.contract_no) parts.push(side.contract_no)
  if (side.amount_2) parts.push(side.amount_2)
  else if (side.amount_1) parts.push(side.amount_1)
  return parts.join(' · ')
}

// ---------- 匹配复核 ----------
const matches = ref([])
const matchesLoading = ref(false)
const allocDrafts = reactive({})

async function loadMatches() {
  if (!props.batch) {
    matches.value = []
    return
  }
  matchesLoading.value = true
  try {
    const { data } = await reconApi.matches(props.batch.id)
    matches.value = data.items || []
    for (const key of Object.keys(allocDrafts)) delete allocDrafts[key]
    for (const row of matches.value) {
      allocDrafts[row.id] = row.allocated_amount === null || row.allocated_amount === undefined ? null : Number(row.allocated_amount)
    }
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    matchesLoading.value = false
  }
}

function setAllocation(row, value) {
  allocDrafts[row.id] = value
}

async function confirmMatch(row) {
  const payload = { status: 'confirmed' }
  const amount = allocDrafts[row.id]
  if (amount !== null && amount !== undefined && amount !== '') {
    payload.allocated_amount = Number(amount).toFixed(2)
  }
  try {
    const { data } = await reconApi.patchMatch(row.id, payload)
    row.status = data.status
    row.status_label = data.status_label
    ElMessage.success('匹配已确认；重跑检查不会覆盖人工裁定')
    await loadMatches()
  } catch {
    /* 拦截器已统一提示（超额/越权 409 均带中文定位） */
  }
}

async function rejectMatch(row) {
  try {
    const { data } = await reconApi.patchMatch(row.id, { status: 'rejected' })
    row.status = data.status
    row.status_label = data.status_label
    ElMessage.success('建议已排除；机检重跑不会重新推荐该三元组')
  } catch {
    /* 拦截器已统一提示 */
  }
}

// 人工新增匹配：认领/核销记录选择器来自事实记录分页接口
const matchDialog = ref(false)
const matchCreating = ref(false)
const matchForm = reactive({ claim_record_id: null, writeoff_record_id: null, allocated_amount: null, note: '' })
const claimOptions = ref([])
const writeoffOptions = ref([])

function recordOption(row) {
  const parts = [`#${row.id}`]
  if (row.doc_no) parts.push(row.doc_no)
  if (row.contract_no) parts.push(row.contract_no)
  parts.push(row.amount_2 ?? '金额未填')
  return parts.join(' · ')
}

async function loadMatchOptions() {
  if (!props.batch) return
  const load = async (kind) => {
    try {
      const { data } = await reconApi.records(props.batch.id, { page: 1, page_size: 200, kind })
      return (data.items || []).map(recordOption)
    } catch {
      return []
    }
  }
  claimOptions.value = await load('claim')
  writeoffOptions.value = await load('writeoff')
}

function openMatchDialog() {
  matchForm.claim_record_id = null
  matchForm.writeoff_record_id = null
  matchForm.allocated_amount = null
  matchForm.note = ''
  void loadMatchOptions()
  matchDialog.value = true
}

async function createMatch() {
  if (!props.batch || matchCreating.value) return
  if (!matchForm.claim_record_id || !matchForm.writeoff_record_id || matchForm.allocated_amount === null) {
    ElMessage.warning('请先选择认领记录、核销记录并填写分摊金额')
    return
  }
  matchCreating.value = true
  try {
    await reconApi.createMatch(props.batch.id, {
      claim_record_id: matchForm.claim_record_id,
      writeoff_record_id: matchForm.writeoff_record_id,
      allocated_amount: Number(matchForm.allocated_amount).toFixed(2),
      note: matchForm.note.trim(),
    })
    ElMessage.success('人工匹配建议已创建')
    matchDialog.value = false
    await loadMatches()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    matchCreating.value = false
  }
}

// ---------- 问题复核 ----------
const issues = ref([])
const issuesLoading = ref(false)

async function loadIssues() {
  if (!props.batch) {
    issues.value = []
    return
  }
  issuesLoading.value = true
  try {
    const { data } = await reconApi.issues(props.batch.id)
    issues.value = data.items || []
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    issuesLoading.value = false
  }
}

const pendingCount = computed(
  () => issues.value.filter((item) => item.status === 'pending' && item.active).length,
)

const issueDialog = ref(false)
const issueCreating = ref(false)
const issueForm = reactive({ category: '', severity: 'warn', note: '', amount_impact: null })

function openIssueDialog() {
  issueForm.category = ''
  issueForm.severity = 'warn'
  issueForm.note = ''
  issueForm.amount_impact = null
  issueDialog.value = true
}

async function createIssue() {
  if (!props.batch || issueCreating.value) return
  if (!issueForm.category.trim()) {
    ElMessage.warning('请填写问题类别')
    return
  }
  issueCreating.value = true
  try {
    await reconApi.createIssue(props.batch.id, {
      category: issueForm.category.trim(),
      severity: issueForm.severity,
      note: issueForm.note.trim(),
      amount_impact: issueForm.amount_impact === null ? null : Number(issueForm.amount_impact).toFixed(2),
      evidence: [],
    })
    ElMessage.success('问题已登记（人工来源，机检不会改动）')
    issueDialog.value = false
    issueForm.category = ''
    issueForm.note = ''
    issueForm.amount_impact = null
    await loadIssues()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    issueCreating.value = false
  }
}

const resolveDialog = ref(false)
const resolveSaving = ref(false)
const resolveStatus = ref('confirmed')
const resolveRowId = ref(null)
const resolveNote = ref('')
const resolveTitle = computed(() => (resolveStatus.value === 'confirmed' ? '确认问题' : '排除问题'))

function askResolve(row, status) {
  resolveRowId.value = row.id
  resolveStatus.value = status
  resolveNote.value = row.note || ''
  resolveDialog.value = true
}

async function submitResolve() {
  if (resolveSaving.value) return
  const row = issues.value.find((item) => item.id === resolveRowId.value)
  if (!row) {
    resolveDialog.value = false
    return
  }
  const trimmed = resolveNote.value.trim()
  if (!trimmed) {
    ElMessage.warning('确认或排除问题必须填写说明')
    return
  }
  resolveSaving.value = true
  try {
    const { data } = await reconApi.patchIssue(row.id, { status: resolveStatus.value, note: trimmed })
    Object.assign(row, data)
    ElMessage.success(resolveStatus.value === 'confirmed' ? '问题已确认' : '问题已排除')
    resolveDialog.value = false
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    resolveSaving.value = false
  }
}

// ---------- 导出（needs_recheck / 未决事项先警示，确认后仍尝试，由后端守卫最终裁决） ----------
const exportBusy = ref('')

async function download(fmt) {
  if (!props.batch || exportBusy.value) return
  if (props.batch.needs_recheck) {
    try {
      await ElMessageBox.confirm(
        '口径参数在上次勾稽检查通过后又被修改，导出会被拒绝；需重新执行勾稽检查后再导出。仍要尝试导出吗？',
        '需重跑勾稽检查',
        { type: 'warning', confirmButtonText: '仍要导出', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  } else if (pendingCount.value > 0) {
    try {
      await ElMessageBox.confirm(
        `当前存在 ${pendingCount.value} 项待确认问题/差异，报告中将显著标注未决事项（草稿提示）。是否继续导出？`,
        '存在未决事项',
        { type: 'warning', confirmButtonText: '继续导出', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  exportBusy.value = fmt
  try {
    const { data } = await reconApi.export(props.batch.id, fmt)
    saveBlob(data, exportFileName(props.batch, fmt))
    ElMessage.success('导出已开始下载')
  } catch {
    /* 拦截器已统一提示（needs_recheck 409 等带中文定位） */
  } finally {
    exportBusy.value = ''
  }
}

// 首次挂载即拉取匹配建议与问题清单（页签 lazy，按 batch.id key 每次只挂一次；加载函数自带守卫与 catch）
onMounted(() => {
  void loadMatches()
  void loadIssues()
})

defineExpose({
  matches,
  matchesLoading,
  loadMatches,
  allocDrafts,
  setAllocation,
  confirmMatch,
  rejectMatch,
  matchDialog,
  matchForm,
  claimOptions,
  writeoffOptions,
  loadMatchOptions,
  openMatchDialog,
  createMatch,
  matchCreating,
  issues,
  issuesLoading,
  loadIssues,
  pendingCount,
  issueDialog,
  issueForm,
  openIssueDialog,
  createIssue,
  issueCreating,
  askResolve,
  submitResolve,
  resolveDialog,
  resolveNote,
  download,
  exportBusy,
})
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0 0 6px;
}
.gap-bottom {
  margin-bottom: 12px;
}
.gap-left-sm {
  margin-left: 6px;
}
.full-width {
  width: 100%;
}
.alloc-input {
  width: 130px;
}
</style>
