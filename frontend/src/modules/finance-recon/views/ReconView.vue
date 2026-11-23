<template>
  <div class="page">
    <HelpLink section="6.17" />
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-head">
          <span>对账批次</span>
          <span class="head-actions">
            <el-button size="small" :loading="batchesLoading" @click="loadBatches">刷新</el-button>
            <el-button size="small" type="primary" @click="openCreate">新建批次</el-button>
          </span>
        </div>
      </template>
      <el-table :data="batches" size="small" empty-text="暂无对账批次：点右上角「新建批次」，填写名称、对账对象与对账日">
        <el-table-column prop="name" label="批次名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="object_tag" label="对账对象" min-width="120" show-overflow-tooltip />
        <el-table-column prop="as_of" label="对账日" width="100" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="batchStatusMeta(row.status).type">{{ batchStatusMeta(row.status).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="需重跑" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.needs_recheck" size="small" type="warning">需重跑</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="160" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="selectBatch(row)">选择</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <template v-if="batch">
      <el-alert
        v-if="needsRecheck"
        :title="recheckHint"
        type="warning"
        :closable="false"
        class="block"
      />
      <el-alert
        v-if="batch.error_summary"
        :title="`批次异常摘要：${batch.error_summary}`"
        type="error"
        :closable="false"
        class="block"
      />
      <el-card shadow="never" class="block">
        <template #header>
          <div class="card-head">
            <span class="batch-title">
              {{ batch.name }}（{{ batch.object_tag || '未填对账对象' }} · 对账日 {{ batch.as_of }} · 容差 {{ batch.tolerance }})
            </span>
            <el-tag size="small" :type="batchStatusMeta(batch.status).type">{{ batchStatusMeta(batch.status).label }}</el-tag>
          </div>
        </template>
        <el-tabs v-model="tab">
          <el-tab-pane label="资料与参数" name="sources" lazy>
            <SourcePanel :key="batch.id" :batch="batch" @refresh="refreshBatch" />
          </el-tab-pane>
          <el-tab-pane label="勾稽结果" name="result" lazy>
            <ResultPanel :key="batch.id" :batch="batch" @refresh="refreshBatch" />
          </el-tab-pane>
          <el-tab-pane label="复核与导出" name="review" lazy>
            <ReviewPanel :key="batch.id" :batch="batch" @refresh="refreshBatch" />
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </template>
    <el-card v-else shadow="never" class="block">
      <p class="empty-hint">从上方选择一个批次后，这里展示「资料与参数 / 勾稽结果 / 复核与导出」三个工作区。</p>
    </el-card>

    <el-dialog v-model="createVisible" title="新建对账批次" width="480px">
      <el-form label-width="90px">
        <el-form-item label="批次名称">
          <el-input v-model="form.name" placeholder="如 2026-11 商户核销对账" maxlength="100" />
        </el-form-item>
        <el-form-item label="对账对象">
          <el-input v-model="form.object_tag" placeholder="商户/项目标签（可选）" maxlength="100" />
        </el-form-item>
        <el-form-item label="对账日">
          <el-date-picker v-model="form.as_of" type="date" value-format="YYYY-MM-DD" placeholder="对账截止日" class="full-width" />
        </el-form-item>
        <el-form-item label="金额容差">
          <el-input-number v-model="form.tolerance" :precision="2" :min="0" :controls="false" class="tolerance-input" />
          <span class="muted">默认 0.01</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="createLoading" :disabled="!canCreate" @click="createBatch">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import HelpLink from '../../../components/HelpLink.vue'
import { batchStatusMeta, reconApi } from '../api'
import SourcePanel from './SourcePanel.vue'
import ResultPanel from './ResultPanel.vue'
import ReviewPanel from './ReviewPanel.vue'

// ---------- 批次列表与选择 ----------
const batches = ref([])
const batchesLoading = ref(false)
const batch = ref(null)
const tab = ref('sources')

async function loadBatches() {
  batchesLoading.value = true
  try {
    const { data } = await reconApi.batches(50)
    batches.value = data.items || []
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    batchesLoading.value = false
  }
}

async function selectBatch(rowOrId) {
  const id = rowOrId && typeof rowOrId === 'object' ? rowOrId.id : rowOrId
  if (!id) return
  try {
    const { data } = await reconApi.batch(id)
    batch.value = data
    tab.value = 'sources'
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function refreshBatch() {
  if (!batch.value) return
  try {
    const { data } = await reconApi.batch(batch.value.id)
    batch.value = data
  } catch {
    /* 拦截器已统一提示 */
  }
}

const needsRecheck = computed(() => !!batch.value?.needs_recheck)
const recheckHint =
  '口径参数或批次口径在上次勾稽检查通过后又被修改，需重新执行勾稽检查后再导出报告（导出前会有警示与 409 守卫）。'

// ---------- 新建批次 ----------
const createVisible = ref(false)
const createLoading = ref(false)
const form = reactive({ name: '', object_tag: '', as_of: null, tolerance: 0.01 })
const canCreate = computed(() => !!form.name.trim() && !!form.as_of)

function openCreate() {
  form.name = ''
  form.object_tag = ''
  form.as_of = null
  form.tolerance = 0.01
  createVisible.value = true
}

async function createBatch() {
  if (!canCreate.value || createLoading.value) return
  createLoading.value = true
  try {
    const { data } = await reconApi.createBatch({
      name: form.name.trim(),
      object_tag: form.object_tag.trim(),
      as_of: form.as_of,
      tolerance: Number(form.tolerance ?? 0.01).toFixed(2),
      contract_scope: [],
    })
    ElMessage.success(`批次「${data.name}」已创建（草稿）`)
    createVisible.value = false
    await loadBatches()
    await selectBatch(data.id)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    createLoading.value = false
  }
}

onMounted(loadBatches)

defineExpose({
  batches,
  batchesLoading,
  batch,
  tab,
  loadBatches,
  selectBatch,
  refreshBatch,
  needsRecheck,
  recheckHint,
  createVisible,
  createLoading,
  form,
  canCreate,
  openCreate,
  createBatch,
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.block {
  margin: 0;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.head-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.batch-title {
  font-weight: 600;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.empty-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0;
}
.full-width {
  width: 100%;
}
.tolerance-input {
  width: 140px;
  margin-right: 8px;
}
</style>
