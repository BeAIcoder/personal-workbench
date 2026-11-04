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
        <el-divider content-position="left">申报期与核心财务数据（万元）</el-divider>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="所属年度"><el-input-number v-model="form.tax_period" :controls="false" :precision="0" :min="2000" :max="2100" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="申报季度">
            <el-select v-model="form.quarter" style="width: 100%">
              <el-option v-for="q in ['Q1', 'Q2', 'Q3', 'Q4']" :key="q" :value="q" :label="q" />
            </el-select>
          </el-form-item></el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="营业收入"><el-input-number v-model="form.revenue" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="其他收入"><el-input-number v-model="form.other_income" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="营业成本"><el-input-number v-model="form.total_costs" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="期间费用"><el-input-number v-model="form.total_expenses" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="工资薪金总额"><el-input-number v-model="form.total_salary" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
        </el-row>

        <el-divider content-position="left">纳税调整项（万元，仅年度汇算计入；季报只留痕）</el-divider>
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
          <el-col :span="8"><el-form-item label="设备当年折旧"><el-input-number v-model="form.equipment_depreciation" :controls="false" :precision="2" style="width: 100%" /></el-form-item></el-col>
        </el-row>

        <el-divider content-position="left">企业属性（影响类型判定与优惠）</el-divider>
        <el-row :gutter="16">
          <el-col :span="6">
            <el-form-item label="高新技术">
              <el-tag :type="isHighTech ? 'success' : 'info'" size="small">{{ isHighTech ? '是（企业档案）' : '否' }}</el-tag>
            </el-form-item>
          </el-col>
          <el-col :span="6"><el-form-item label="限制行业"><el-switch v-model="form.is_restricted_industry" /></el-form-item></el-col>
          <el-col :span="6"><el-form-item label="企业类型">
            <el-select v-model="form.enterprise_type" style="width: 100%">
              <el-option value="一般企业" label="一般企业" />
              <el-option value="小型微利企业" label="小型微利企业" />
              <el-option value="集成电路企业" label="集成电路企业" />
              <el-option value="工业母机企业" label="工业母机企业" />
            </el-select>
          </el-form-item></el-col>
          <el-col :span="6">
            <el-form-item label="优惠选择">
              <el-select v-model="form.preferential_choice" style="width: 100%">
                <el-option value="" label="不指定（仅一种优惠时自动）" />
                <el-option value="high_tech" label="高新技术企业 15%" />
                <el-option value="small_micro" label="小型微利企业 5%" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <p class="hint">
          高新技术资格以企业档案为准（在「企业档案」页维护）；同时符合高新与小微时两项优惠不得叠加，必须在此明确选择其一。
          季度预缴（A200000）按第 8 行实际利润额计税，不纳入上面填写的纳税调整——调整只随记录留痕，留待年度汇算清缴。
        </p>

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
        <el-col :span="6"><el-statistic title="年度测算应纳税所得额(万)" :value="result.taxable_income_after_loss_wan?.toFixed(2)" /></el-col>
        <el-col :span="6"><el-statistic title="年度测算应纳税额(万，含调整)" :value="result.tax_amount_wan?.toFixed(2)" /></el-col>
      </el-row>
      <el-row :gutter="16" style="margin-top: 12px">
        <el-col :span="12">
          <el-statistic title="季度预缴应纳税所得额(万，不含调整)" :value="result.prepay_taxable_income_wan?.toFixed(2)" />
        </el-col>
        <el-col :span="12">
          <el-statistic title="季度预缴应纳税额(万，与保存/导出一致)" :value="result.prepay_tax_amount_wan?.toFixed(2)" />
        </el-col>
      </el-row>
      <p class="hint">{{ result.prepay_note }}</p>
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
        <el-tag v-for="p in result.preferential_policies" :key="p.name || p.policy_basis" type="success" style="margin: 0 8px 8px 0">
          {{ p.name || p.policy_basis }}<template v-if="p.amount_wan">（减免 ¥{{ p.amount_wan }} 万）</template>
        </el-tag>      </template>
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
const isHighTech = ref(false)

const form = reactive({
  tax_period: new Date().getFullYear(),
  quarter: `Q${Math.floor(new Date().getMonth() / 3) + 1}`,
  revenue: 0, other_income: 0, total_costs: 0, total_expenses: 0, total_salary: 0,
  business_entertainment: 0, advertising_expense: 0, donation_expense: 0, rnd_expense: 0,
  welfare_expense: 0, education_expense: 0, union_expense: 0, penalties: 0, equipment_purchase: 0,
  equipment_depreciation: 0,
  employee_count: 0, total_assets: 0, industry: '一般行业', is_restricted_industry: false,
  is_high_tech: false, enterprise_type: '一般企业', advertising_industry: '一般行业',
  preferential_choice: '',
})

async function loadEnterprise() {
  loading.value = true
  try {
    const { data } = await api.get(`/finance/tax/enterprises/${route.params.enterpriseId}`)
    enterprise.value = data
    form.employee_count = data.employee_count || 0
    form.total_assets = data.total_assets || 0
    form.is_restricted_industry = !!data.is_restricted_industry
    // 高新资格以企业档案为准：计算与保存必须同源，页面不再提供临时开关
    isHighTech.value = !!data.is_high_tech
    form.is_high_tech = isHighTech.value
    if (data.industry_type) form.industry = data.industry_type
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

async function calculate() {
  calculating.value = true
  try {
    const { data } = await api.post('/finance/tax/tax-returns/calculate', {
      enterprise_id: Number(route.params.enterpriseId),
      year: form.tax_period,
      ...form,
    })
    result.value = data
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    calculating.value = false
  }
}

async function saveReturn() {
  saving.value = true
  try {
    await api.post('/finance/tax/tax-returns/', {
      enterprise_id: Number(route.params.enterpriseId),
      tax_period: String(form.tax_period),
      quarter: form.quarter,
      report_type: 'A类',
      total_revenue: form.revenue,
      other_income: form.other_income,
      total_costs: form.total_costs,
      total_expenses: form.total_expenses,
      tax_adjustments: result.value?.net_adjustment_wan || 0,
      preferential_choice: form.preferential_choice,
    })
    ElMessage.success(
      Number(result.value?.net_adjustment_wan || 0)
        ? '已保存为申报记录（季报按第 8 行实际利润额计税，纳税调整已留痕、留待年度汇算）'
        : '已保存为申报记录',
    )
    router.push({ name: 'finance-tax-returns' })
  } catch {
    /* 拦截器已统一提示（同期间重复会提示用「更正」或删除草稿） */
  } finally {
    saving.value = false
  }
}

onMounted(loadEnterprise)
</script>

<style scoped>
.calc-form :deep(.el-input-number) {
  width: 100%;
}

.reason {
  margin: 12px 0 0;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.step-detail .formula {
  color: var(--el-text-color-secondary);
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
