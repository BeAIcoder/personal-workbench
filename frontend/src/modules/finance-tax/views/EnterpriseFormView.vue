<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>{{ isEdit ? '编辑企业' : '新建企业' }}</span>
          <el-button size="small" @click="$router.push({ name: 'finance-tax' })">返回列表</el-button>
        </div>
      </template>
      <el-form :model="form" :rules="rules" ref="formRef" label-width="120px" style="max-width: 560px">
        <el-form-item label="企业名称" prop="name">
          <el-input v-model="form.name" maxlength="200" />
        </el-form-item>
        <el-form-item label="纳税人识别号" prop="tax_number">
          <el-input v-model="form.tax_number" maxlength="50" :disabled="isEdit" />
        </el-form-item>
        <el-form-item label="行业类型">
          <el-input v-model="form.industry_type" maxlength="100" placeholder="如：软件、零售、制造" />
        </el-form-item>
        <el-form-item label="从业人数">
          <el-input-number v-model="form.employee_count" :min="0" :controls="false" style="width: 160px" />
        </el-form-item>
        <el-form-item label="资产总额(万元)">
          <el-input-number v-model="form.total_assets" :min="0" :precision="2" :controls="false" style="width: 160px" />
        </el-form-item>
        <el-form-item label="是否限制行业">
          <el-switch v-model="form.is_restricted_industry" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const route = useRoute()
const router = useRouter()
const isEdit = computed(() => !!route.params.id)
const loading = ref(false)
const submitting = ref(false)
const formRef = ref(null)

const form = reactive({
  name: '', tax_number: '', industry_type: '',
  employee_count: undefined, total_assets: undefined, is_restricted_industry: false,
})

const rules = {
  name: [{ required: true, message: '请输入企业名称', trigger: 'blur' }],
  tax_number: [{ required: true, message: '请输入纳税人识别号', trigger: 'blur' }],
}

async function load() {
  if (!isEdit.value) return
  loading.value = true
  try {
    const { data } = await api.get(`/finance/tax/enterprises/${route.params.id}`)
    Object.assign(form, data)
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

async function submit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    if (isEdit.value) {
      await api.put(`/finance/tax/enterprises/${route.params.id}`, form)
    } else {
      await api.post('/finance/tax/enterprises/', form)
    }
    ElMessage.success('已保存')
    router.push({ name: 'finance-tax' })
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
