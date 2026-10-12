<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>税额计算（{{ enterprise.name || '...' }}）</span>
          <el-button size="small" @click="$router.push({ name: 'finance-tax' })">返回列表</el-button>
        </div>
      </template>

      <el-form :model="form" label-width="150px" class="calc-form">
        <el-divider content-position="left">核心财务数据（万元）</el-divider>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="营业收入"><el-input-number v-model="form.revenue" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="其他收入"><el-input-number v-model="form.other_income" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="营业成本"><el-input-number v-model="form.total_costs" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="期间费用"><el-input-number v-model="form.total_expenses" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="工资薪金总额"><el-input-number v-model="form.total_salary" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
        </el-row>

        <el-divider content-position="left">纳税调整项（万元）</el-divider>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="业务招待费"><el-input-number v-model="form.business_entertainment" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="广告费"><el-input-number v-model="form.advertising_expense" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="公益性捐赠"><el-input-number v-model="form.donation_expense" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="研发费用"><el-input-number v-model="form.rnd_expense" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="职工福利费"><el-input-number v-model="form.welfare_expense" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="职工教育经费"><el-input-number v-model="form.education_expense" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="工会经费"><el-input-number v-model="form.union_expense" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="罚款滞纳金"><el-input-number v-model="form.penalties" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="设备购置"><el-input-number v-model="form.equipment_purchase" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
        </el-row>

        <el-divider content-position="left">企业属性（影响类型判定与优惠）</el-divider>
        <el-row :gutter="16">
          <el-col :span="6"><el-form-item label="高新技术"><el-switch v-model="form.is_high_tech" /></el-form-item></el-col>
          <el-col :span="6"><el-form-item label="限制行业"><el-switch v-model="form.is_restricted_industry" /></el-form-item></el-col>
          <el-col :span="6"><el-form-item label="企业类型">
            <el-select v-model="form.enterprise_type" style="width: 100%">
              <el-option value="一般企业" label="一般企业" />
              <el-option value="小型微利企业" label="小型微利企业" />
            </el-select>
          </el-form-item></el-col>
        </el-row>

        <el-form-item>
          <el-button type="primary" :loading="calculating" @click="calculate">开始计算</el-button>
          <el-button v-if="result" :loading="saving" @click="saveReturn">保存为申报记录</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card v-if="result" shadow="never" style="margin-top: 16px">
      <template #header><span>计算结果</span></template>
      <el-row :gutter="16">
        <el-col :span="6"><el-statistic title="企业类型判定" :value="result.enterprise_tax_type" /></el-col>
        <el-col :span="6"><el-statistic title="适用税率" :value="(result.applicable_tax_rate * 100).toFixed(2) + '%'" /></el-col>
        <el-col :span="6"><el-statistic title="应纳税所得额(万)" :value="result.taxable_income_after_loss_wan?.toFixed(2)" /></el-col>
        <el-col :span="6"><el-statistic title="应纳税额(万)" :value="result.tax_amount_wan?.toFixed(2)" /></el-col>
      </el-row>
      <el-descriptions :column="3" border size="small" style="margin-top: 16px">
        <el-descriptions-item label="利润总额(万)">{{ result.accounting_profit_wan?.toFixed(2) }}</el-descriptions-item>
        <el-descriptions-item label="纳税调增(万)">{{ result.total_adjustment_increase_wan?.toFixed(2) }}</el-descriptions-item>
        <el-descriptions-item label="纳税调减(万)">{{ result.total_adjustment_decrease_wan?.toFixed(2) }}</el-descriptions-item>
        <el-descriptions-item label="弥补亏损(万)">{{ result.loss_carry_forward_used_wan?.toFixed(2) }}</el-descriptions-item>
        <el-descriptions-item label="小型微利">
          <el-tag :type="result.is_small_micro ? 'success' : 'info'" size="small">{{ result.is_small_micro ? '是' : '否' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="高新技术">
          <el-tag :type="result.is_high_tech ? 'success' : 'info'" size="small">{{ result.is_high_tech ? '是' : '否' }}</el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <p class="reason"><b>计税说明：</b>{{ result.tax_reason }}</p>

      <h4>计算过程</h4>
      <el-steps direction="vertical" :active="result.calculation_steps?.length || 0">
        <el-step v-for="s in result.calculation_steps" :key="s.step" :title="s.title" :description="s.result">
          <template #description>
            <div class="step-detail">
              <div v-if="s.formula" class="formula">{{ s.formula }}</div>
              <div v-if="s.values">
                <span v-for="(v, k) in s.values" :key="k" class="kv">{{ k }}：{{ v }}</span>
              </div>
              <div v-if="s.adjustments">
                <span v-for="(a, i) in s.adjustments" :key="i" class="kv">{{ a }}</span>
              </div>
              <div class="result-line">{{ s.result }}</div>
            </div>
          </template>
        </el-step>
      </el-steps>

      <template v-if="result.preferential_policies?.length">
        <h4>适用税收优惠</h4>
        <el-tag v-for="p in result.preferential_policies" :key="p.policy_number || p.title" type="success" style="margin: 0 8px 8px 0">
          {{ p.title || p.policy_number }}
        </el-tag>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../../api'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const calculating = ref(false)
const saving = ref(false)
const enterprise = ref({})
const result = ref(null)

const form = reactive({
  revenue: 0, other_income: 0, total_costs: 0, total_expenses: 0, total_salary: 0,
  business_entertainment: 0, advertising_expense: 0, donation_expense: 0, rnd_expense: 0,
  welfare_expense: 0, education_expense: 0, union_expense: 0, penalties: 0, equipment_purchase: 0,
  employee_count: 0, total_assets: 0, industry: '一般行业', is_restricted_industry: false,
  is_high_tech: false, enterprise_type: '一般企业', advertising_industry: '一般行业',
})

async function loadEnterprise() {
  loading.value = true
  try {
    const { data } = await api.get(`/finance/tax/enterprises/${route.params.enterpriseId}`)
    enterprise.value = data
    form.employee_count = data.employee_count || 0
    form.total_assets = data.total_assets || 0
    form.is_restricted_industry = !!data.is_restricted_industry
    if (data.industry_type) form.industry = data.industry_type
  } catch {
    ElMessage.error('企业不存在')
  } finally {
    loading.value = false
  }
}

async function calculate() {
  calculating.value = true
  try {
    const { data } = await api.post('/finance/tax/tax-returns/calculate', {
      enterprise_id: Number(route.params.enterpriseId),
      year: new Date().getFullYear(),
      ...form,
    })
    result.value = data
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '计算失败')
  } finally {
    calculating.value = false
  }
}

async function saveReturn() {
  saving.value = true
  try {
    await api.post('/finance/tax/tax-returns/', {
      enterprise_id: Number(route.params.enterpriseId),
      tax_period: String(new Date().getFullYear()),
      report_type: 'A类',
      total_revenue: form.revenue,
      other_income: form.other_income,
      total_costs: form.total_costs,
      total_expenses: form.total_expenses,
      tax_adjustments: result.value?.net_adjustment_wan || 0,
    })
    ElMessage.success('已保存为申报记录')
    router.push({ name: 'finance-tax-returns' })
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadEnterprise)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.calc-form :deep(.el-input-number) {
  width: 100%;
}

.reason {
  margin: 12px 0 0;
  color: #606266;
  font-size: 13px;
}

.step-detail .formula {
  color: #909399;
  font-size: 12px;
}

.step-detail .kv {
  display: inline-block;
  margin-right: 16px;
  font-size: 13px;
}

.step-detail .result-line {
  font-weight: 600;
}
</style>
