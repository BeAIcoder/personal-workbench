<template>
  <div class="panel">
    <el-descriptions :column="2" border size="small">
      <el-descriptions-item label="项目编码">{{ item.project_code }}</el-descriptions-item>
      <el-descriptions-item label="项目名称">{{ item.name || '—' }}</el-descriptions-item>
      <el-descriptions-item label="所属项目">{{ item.project || '—' }}</el-descriptions-item>
      <el-descriptions-item label="项目状态">{{ item.status_label || itemStatusMeta(item.status).label }}</el-descriptions-item>
      <el-descriptions-item label="含税金额">{{ formatMoney(item.amount_incl_tax) }}</el-descriptions-item>
      <el-descriptions-item label="税率">{{ formatRate(item.tax_rate) }}</el-descriptions-item>
      <el-descriptions-item label="净额（含税÷(1+税率)）">{{ formatMoney(item.amount_net) }}</el-descriptions-item>
      <el-descriptions-item label="税额（含税−净额）">{{ formatMoney(item.tax_amount) }}</el-descriptions-item>
      <el-descriptions-item label="起摊月">{{ item.start_month }}</el-descriptions-item>
      <el-descriptions-item label="摊销月数">{{ item.months }}</el-descriptions-item>
      <el-descriptions-item label="摊销到期月">{{ item.amortization_end_month }}</el-descriptions-item>
      <el-descriptions-item label="租赁到期日">{{ item.lease_end_date }}</el-descriptions-item>
      <el-descriptions-item label="备注" :span="2">{{ item.remark || '—' }}</el-descriptions-item>
    </el-descriptions>

    <div class="actions">
      <el-button
        size="small"
        type="primary"
        :disabled="item.status !== 'draft'"
        title="归档后项目参数只读"
        @click="startEdit"
      >
        修改参数
      </el-button>
      <span v-if="item.status !== 'draft'" class="muted">项目已归档：参数只读，如需调整请新建项目。</span>
      <span v-else class="muted">
        修改经济要素（含税金额/税率/起摊月/月数/租赁到期日）后，已有测算会标记为「已过期」，需重新测算。
      </span>
    </div>

    <el-dialog v-model="editVisible" title="修改项目参数" width="640px">
      <el-form label-width="110px" @submit.prevent>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="项目编码" required>
              <el-input v-model="form.project_code" maxlength="64" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="项目名称" required>
              <el-input v-model="form.name" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="所属项目" required>
              <el-input v-model="form.project" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="含税金额" required>
              <el-input v-model="form.amount_incl_tax" placeholder="含税总额，两位小数" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="税率" required>
              <el-input v-model="form.tax_rate" placeholder="小数，例如 6% 填 0.06" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="摊销月数" required>
              <el-input-number v-model="form.months" :min="1" :max="600" :step="1" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="起摊月" required>
              <el-date-picker
                v-model="form.start_month"
                type="month"
                value-format="YYYY-MM"
                placeholder="YYYY-MM"
                class="full-width"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="租赁到期日" required>
              <el-date-picker
                v-model="form.lease_end_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="YYYY-MM-DD"
                class="full-width"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="摊销到期月">
          <span class="muted">{{ previewEndMonth || '按起摊月与月数自动推算' }}</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="2000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!canSave" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  amortizationEndMonth,
  buildItemPayload,
  deferredApi,
  formatMoney,
  formatRate,
  itemStatusMeta,
  itemToForm,
} from '../api'

const props = defineProps({
  item: { type: Object, required: true },
})
const emit = defineEmits(['refresh'])

const editVisible = ref(false)
const saving = ref(false)
const form = reactive(itemToForm(props.item))

const canSave = computed(
  () =>
    Boolean(String(form.project_code || '').trim()) &&
    Boolean(String(form.name || '').trim()) &&
    Boolean(String(form.project || '').trim()) &&
    form.amount_incl_tax !== null &&
    form.amount_incl_tax !== '' &&
    form.tax_rate !== null &&
    form.tax_rate !== '' &&
    Boolean(form.start_month) &&
    Number(form.months) >= 1 &&
    Boolean(form.lease_end_date),
)

const previewEndMonth = computed(() => amortizationEndMonth(form.start_month, form.months))

function startEdit() {
  if (props.item.status !== 'draft') {
    ElMessage.warning('项目已归档，参数只读')
    return
  }
  Object.assign(form, itemToForm(props.item))
  editVisible.value = true
}

async function save() {
  if (saving.value) return
  let payload
  try {
    payload = buildItemPayload(form)
  } catch (error) {
    ElMessage.warning(error.message)
    return
  }
  saving.value = true
  try {
    await deferredApi.patchItem(props.item.id, payload)
    ElMessage.success('项目参数已保存')
    editVisible.value = false
    emit('refresh')
  } catch {
    /* 拦截器已统一提示；编码重复等由后端返回中文 409/400 */
  } finally {
    saving.value = false
  }
}

defineExpose({
  props,
  editVisible,
  saving,
  form,
  canSave,
  previewEndMonth,
  startEdit,
  save,
  formatMoney,
  formatRate,
  itemStatusMeta,
})
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.full-width {
  width: 100%;
}
</style>
