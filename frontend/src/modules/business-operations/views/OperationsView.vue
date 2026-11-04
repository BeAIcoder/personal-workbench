<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>营运管理</span>
          <div>
            <el-button size="small" @click="loadAll">刷新</el-button>
            <el-button size="small" @click="exportFile('contracts.xlsx')">导出合同</el-button>
            <el-button size="small" @click="exportFile('bills.xlsx')">导出账单</el-button>
            <el-button size="small" @click="exportOrders">导出工单</el-button>
            <AskAiButton :prompt="aiPrompt" />
          </div>
        </div>
      </template>

      <el-row :gutter="12" class="kpi">
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">履约合同 / 出租面积</div><div class="kpi-value">{{ summary.contracts_active }} / {{ fmt(summary.leased_area) }}</div><div class="kpi-sub">欠费合同 {{ summary.arrears_contracts }} · 已过期履约 {{ summary.expired_active_contracts }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">应收合计</div><div class="kpi-value">¥{{ fmt(summary.receivable) }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">收缴率 / 欠费</div><div class="kpi-value">{{ pct(summary.collection_rate) }} / ¥{{ fmt(summary.arrears) }}</div></div></el-col>
        <el-col :span="6"><div class="kpi-box"><div class="kpi-label">{{ expiringDays }} 天到期 / 未结工单</div><div class="kpi-value">{{ expiring.length }} / {{ summary.open_work_orders }}</div><div class="kpi-sub">本页超期工单 {{ overdueOrderCount }}</div></div></el-col>
      </el-row>

      <el-tabs v-model="tab" class="mt8">
        <el-tab-pane label="合同台账" name="contracts">
          <div class="filters">
            <el-input v-model="contractQuery.keyword" placeholder="合同编号" size="small" clearable style="width: 150px" @keyup.enter="searchContracts" />
            <el-input v-model="contractQuery.tenant" placeholder="租户" size="small" clearable style="width: 150px" />
            <el-select v-model="contractQuery.status" placeholder="状态" size="small" clearable style="width: 120px">
              <el-option label="履约中" value="active" /><el-option label="已终止" value="terminated" />
            </el-select>
            <el-button size="small" type="primary" @click="searchContracts">筛选</el-button>
            <el-button size="small" type="success" @click="openContract()">新建合同</el-button>
            <el-select v-model="expiringDays" size="small" style="width: 110px" @change="loadExpiring">
              <el-option :value="30" label="30 天到期" /><el-option :value="60" label="60 天到期" /><el-option :value="90" label="90 天到期" />
            </el-select>
          </div>
          <el-alert v-if="expiring.length" :title="`${expiringDays} 天内到期合同 ${expiring.length} 份`" type="warning" :closable="false" class="mb8">
            <span v-for="c in expiring" :key="c.id" class="expire-item">{{ c.contract_no }}（{{ c.end_date }}）</span>
          </el-alert>
          <el-table :data="contracts" size="small" border>
            <el-table-column prop="contract_no" label="合同编号" width="140" />
            <el-table-column prop="tenant" label="租户" min-width="150" show-overflow-tooltip />
            <el-table-column prop="unit_no" label="铺位" width="90" />
            <el-table-column label="面积" width="90" align="right"><template #default="{ row }">{{ fmt(row.area) }}</template></el-table-column>
            <el-table-column label="起租" width="110"><template #default="{ row }">{{ row.start_date }}</template></el-table-column>
            <el-table-column label="到期" width="110">
              <template #default="{ row }">
                {{ row.end_date }}
                <el-tag v-if="row.expiring" size="small" type="warning">临期</el-tag>
                <el-tag v-else-if="row.expired" size="small" type="danger">已过期</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="月租金/物业" width="140" align="right"><template #default="{ row }">¥{{ fmt(row.monthly_total) }}</template></el-table-column>
            <el-table-column label="状态" width="90"><template #default="{ row }">{{ row.status_label }}</template></el-table-column>
            <el-table-column label="操作" width="200">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openContract(row)">编辑</el-button>
                <el-button size="small" text @click="openBill(row)">生成账单</el-button>
                <el-button size="small" text type="danger" @click="removeContract(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination class="pager" background layout="total, prev, pager, next" :total="contractTotal" :page-size="contractQuery.page_size" :current-page="contractQuery.page" @current-change="changeContractPage" />
        </el-tab-pane>

        <el-tab-pane label="账单收缴" name="bills">
          <div class="filters">
            <el-date-picker v-model="billQuery.period" type="month" value-format="YYYY-MM" placeholder="所属期" size="small" style="width: 130px" />
            <el-select v-model="billQuery.status" placeholder="状态" size="small" clearable style="width: 120px">
              <el-option label="未收" value="unpaid" /><el-option label="部分收款" value="partial" /><el-option label="已收清" value="paid" />
            </el-select>
            <el-button size="small" type="primary" @click="searchBills">筛选</el-button>
            <el-date-picker v-model="agingAsOf" type="date" value-format="YYYY-MM-DD" placeholder="账龄截止日" size="small" style="width: 145px" @change="loadAging" />
            <el-button size="small" @click="loadAging">查询账龄</el-button>
          </div>
          <div class="aging-grid mb8">
            <div v-for="(label, key) in agingBucketLabels" :key="key" class="aging-bucket">
              <strong>{{ label }}</strong>
              <span>¥{{ fmt(aging.buckets[key].amount) }}</span>
              <small>{{ aging.buckets[key].count }} 笔 / {{ aging.buckets[key].contracts }} 合同</small>
            </div>
          </div>
          <el-alert v-if="agingWarning" :title="agingWarning" type="error" :closable="false" class="mb8" />
          <el-table :data="aging.items" size="small" border max-height="260" class="mb8">
            <el-table-column prop="contract_no" label="合同编号" width="130" />
            <el-table-column prop="tenant" label="租户" min-width="130" />
            <el-table-column prop="period" label="所属期" width="90" />
            <el-table-column label="欠费" width="110" align="right"><template #default="{ row }">¥{{ fmt(row.arrears) }}</template></el-table-column>
            <el-table-column prop="due_date" label="应收日期" width="105" />
            <el-table-column prop="overdue_days" label="逾期天数" width="90" align="right" />
            <el-table-column label="账龄" width="100"><template #default="{ row }">{{ agingBucketLabels[row.bucket] }}</template></el-table-column>
          </el-table>
          <el-table :data="bills" size="small" border>
            <el-table-column prop="period" label="所属期" width="100" />
            <el-table-column prop="contract_no" label="合同编号" width="140" />
            <el-table-column prop="tenant" label="租户" min-width="140" show-overflow-tooltip />
            <el-table-column label="应收" width="110" align="right"><template #default="{ row }">¥{{ fmt(row.total_amount) }}</template></el-table-column>
            <el-table-column label="已收" width="110" align="right"><template #default="{ row }">¥{{ fmt(row.paid_amount) }}</template></el-table-column>
            <el-table-column label="欠费" width="110" align="right"><template #default="{ row }">¥{{ fmt(row.arrears) }}</template></el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }"><el-tag size="small" :type="billTag(row.status)">{{ row.status_label }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="due_date" label="应收日期" width="110" />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button size="small" text type="primary" :disabled="row.status === 'paid'" @click="openPayment(row)">收款</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination class="pager" background layout="total, prev, pager, next" :total="billTotal" :page-size="billQuery.page_size" :current-page="billQuery.page" @current-change="changeBillPage" />
        </el-tab-pane>

        <el-tab-pane label="工单" name="orders">
          <div class="filters">
            <el-input v-model="orderQuery.keyword" placeholder="描述关键词" size="small" clearable style="width: 170px" @keyup.enter="searchOrders" />
            <el-select v-model="orderQuery.status" placeholder="状态" size="small" clearable style="width: 120px">
              <el-option v-for="s in orderStatuses" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
            <el-select v-model="orderQuery.type" placeholder="类型" size="small" clearable style="width: 110px">
              <el-option label="报修" value="repair" /><el-option label="投诉" value="complaint" /><el-option label="巡场" value="inspection" />
            </el-select>
            <el-button size="small" type="primary" @click="searchOrders">筛选</el-button>
            <el-button size="small" type="success" @click="openOrder()">新建工单</el-button>
            <el-button size="small" @click="exportOrders">导出当前筛选</el-button>
          </div>
          <el-alert v-if="overdueOrderCount" :title="`本页有 ${overdueOrderCount} 个工单已超过要求完成时间`" type="error" :closable="false" class="mb8" />
          <el-table :data="orders" size="small" border>
            <el-table-column prop="code" label="工单号" width="100" />
            <el-table-column prop="type_label" label="类型" width="80" />
            <el-table-column prop="location" label="位置" width="120" />
            <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
            <el-table-column prop="priority_label" label="优先级" width="80" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag size="small" :type="orderTag(row.status)">{{ row.status_label }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="assignee" label="责任人" width="90" />
            <el-table-column prop="parent_code" label="父工单" width="105" />
            <el-table-column prop="equipment_code" label="设备编号" width="110" />
            <el-table-column prop="contract_no" label="关联合同" width="120" />
            <el-table-column prop="created_at" label="创建时间" width="155" />
            <el-table-column label="要求完成 / SLA" width="175">
              <template #default="{ row }">{{ row.due_at || '—' }} <el-tag v-if="row.overdue" size="small" type="danger">超期</el-tag></template>
            </el-table-column>
            <el-table-column prop="closed_at" label="首次完成时间" width="155" />
            <el-table-column label="操作" width="200">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="openOrder(row)">处理</el-button>
                <el-button size="small" text @click="openFollowups(row)">跟进记录</el-button>
                <el-button size="small" text type="danger" @click="removeOrder(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination class="pager" background layout="total, prev, pager, next" :total="orderTotal" :page-size="orderQuery.page_size" :current-page="orderQuery.page" @current-change="changeOrderPage" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-dialog v-model="contractDialog" :title="contractForm.id ? '编辑合同' : '新建合同'" width="640px">
      <el-form :model="contractForm" label-width="100px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="合同编号" required><el-input v-model="contractForm.contract_no" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="租户" required><el-input v-model="contractForm.tenant" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="关联铺位"><el-select v-model="contractForm.unit_id" clearable filterable remote :remote-method="searchUnitOptions" :loading="unitOptionsLoading" placeholder="从招商铺位选择（可选）" style="width: 100%"><el-option v-for="u in unitOptions" :key="u.id" :label="u.label" :value="u.id" /><el-option v-if="unitOptionsHint" :value="null" disabled :label="unitOptionsHint" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="铺位号(手填)"><el-input v-model="contractForm.unit_no" placeholder="未关联铺位时手工填写" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="面积(㎡)"><el-input-number v-model="contractForm.area" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="开始日期" required><el-date-picker v-model="contractForm.start_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="结束日期" required><el-date-picker v-model="contractForm.end_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="月租金"><el-input-number v-model="contractForm.rent_monthly" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="月物业费"><el-input-number v-model="contractForm.property_fee_monthly" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="押金"><el-input-number v-model="contractForm.deposit" :min="0" :controls="false" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="状态"><el-select v-model="contractForm.status"><el-option label="履约中" value="active" /><el-option label="已终止" value="terminated" /></el-select></el-form-item></el-col>
        </el-row>
        <el-form-item label="备注"><el-input v-model="contractForm.note" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="contractDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveContract">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="billDialog" title="生成账单" width="460px">
      <el-form label-width="90px">
        <el-form-item label="合同"><el-input :model-value="billForm.contract_label" disabled /></el-form-item>
        <el-form-item label="所属期"><el-date-picker v-model="billForm.period" type="month" value-format="YYYY-MM" style="width: 100%" /></el-form-item>
        <el-form-item label="其他费用"><el-input-number v-model="billForm.other_amount" :min="0" :controls="false" style="width: 100%" /></el-form-item>
        <el-form-item label="应收日期"><el-date-picker v-model="billForm.due_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="billDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="generateBill">生成</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="paymentDialog" title="登记收款" width="420px">
      <el-form label-width="90px">
        <el-form-item label="账单"><el-input :model-value="paymentForm.label" disabled /></el-form-item>
        <el-form-item label="收款金额"><el-input-number v-model="paymentForm.amount" :min="0.01" :controls="false" style="width: 100%" /></el-form-item>
        <el-form-item label="收款日期"><el-date-picker v-model="paymentForm.paid_at" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="paymentDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="payBill">确认收款</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="orderDialog" :title="orderForm.id ? '处理工单' : '新建工单'" width="560px">
      <el-form :model="orderForm" label-width="90px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="类型"><el-select v-model="orderForm.type"><el-option label="报修" value="repair" /><el-option label="投诉" value="complaint" /><el-option label="巡场" value="inspection" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="优先级"><el-select v-model="orderForm.priority"><el-option label="低" value="low" /><el-option label="中" value="medium" /><el-option label="高" value="high" /><el-option label="紧急" value="urgent" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="位置"><el-input v-model="orderForm.location" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="租户"><el-input v-model="orderForm.tenant" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="状态"><el-select v-model="orderForm.status"><el-option v-for="s in orderStatuses" :key="s.value" :label="s.label" :value="s.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="责任人"><el-input v-model="orderForm.assignee" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="要求完成"><el-date-picker v-model="orderForm.due_at" type="datetime" value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="父工单"><el-select v-model="orderForm.parent_id" clearable filterable remote :remote-method="searchOrderOptions" :loading="orderOptionsLoading" style="width: 100%"><el-option v-for="item in orderOptions.filter(o => o.id !== orderForm.id)" :key="item.id" :label="orderOptionLabel(item)" :value="item.id" /><el-option v-if="orderOptionsHint" :value="null" disabled :label="orderOptionsHint" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="关联设备"><el-select v-model="orderForm.equipment_id" clearable filterable remote :remote-method="searchEquipmentOptions" :loading="equipmentOptionsLoading" placeholder="可选" style="width: 100%"><el-option v-for="item in equipmentOptions" :key="item.id" :label="equipmentOptionLabel(item)" :value="item.id" /><el-option v-if="equipmentOptionsHint" :value="null" disabled :label="equipmentOptionsHint" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="关联合同"><el-select v-model="orderForm.contract_id" clearable filterable placeholder="可选" style="width: 100%"><el-option v-for="item in contracts" :key="item.id" :label="`${item.contract_no} · ${item.tenant}`" :value="item.id" /></el-select></el-form-item></el-col>
        </el-row>
        <el-form-item label="描述" required><el-input v-model="orderForm.description" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="orderDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveOrder">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="followupDialog" :title="`工单跟进 · ${followupOrder.code}`" width="720px">
      <el-table :data="followups" size="small" border max-height="280" class="mb8">
        <el-table-column prop="handled_at" label="处理时间" width="165" />
        <el-table-column prop="content" label="处理内容" min-width="180" show-overflow-tooltip />
        <el-table-column prop="result" label="结果" min-width="150" show-overflow-tooltip />
        <el-table-column label="满意度" width="80"><template #default="{ row }">{{ row.satisfaction ?? '—' }}</template></el-table-column>
      </el-table>
      <el-form :model="followupForm" label-width="90px">
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="处理时间"><el-date-picker v-model="followupForm.handled_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="满意度"><el-input-number v-model="followupForm.satisfaction" :min="1" :max="5" :controls="false" style="width: 100%" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="处理内容" required><el-input v-model="followupForm.content" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="处理结果"><el-input v-model="followupForm.result" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="followupDialog = false">关闭</el-button>
        <el-button type="primary" :loading="saving" @click="saveFollowup">新增跟进</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../../../api'
import { optionCapHint, optionParams, ensureOption, createRemoteOptionSearch } from '../../../utils/remoteOptions.js'
import AskAiButton from '../../../components/assistant/AskAiButton.vue'

const aiPrompt = computed(() => '请分析营运台账：在租合同与账龄结构、收缴率、超期工单与 SLA 风险，并指出数据质量问题')

const loading = ref(false)
const saving = ref(false)
const tab = ref('contracts')
const orderStatuses = [
  { value: 'open', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'done', label: '已完成' },
  { value: 'closed', label: '已关闭' },
]
const agingBucketLabels = { not_due: '未到期', '1_30': '1–30 天', '31_90': '31–90 天', over_90: '超 90 天' }

const summary = ref({
  contracts_active: 0, leased_area: 0, receivable: 0, received: 0, arrears: 0,
  collection_rate: 0, arrears_contracts: 0, expiring_30: 0,
  expired_active_contracts: 0, open_work_orders: 0,
})
const contracts = ref([])
const bills = ref([])
const orders = ref([])
const orderOptions = ref([])
const orderOptionsTotal = ref(0)
const orderOptionsLoading = ref(false)
const orderOptionsHint = computed(() => optionCapHint(orderOptions.value, orderOptionsTotal.value))
const equipmentOptions = ref([])
const equipmentOptionsTotal = ref(0)
const equipmentOptionsLoading = ref(false)
const equipmentOptionsHint = computed(() => optionCapHint(equipmentOptions.value, equipmentOptionsTotal.value))
const expiring = ref([])
const contractTotal = ref(0)
const billTotal = ref(0)
const orderTotal = ref(0)
const expiringDays = ref(30)
const agingAsOf = ref(todayLocal())
const emptyAging = () => ({
  as_of: '', items: [],
  buckets: {
    not_due: { amount: 0, count: 0, contracts: 0 },
    '1_30': { amount: 0, count: 0, contracts: 0 },
    '31_90': { amount: 0, count: 0, contracts: 0 },
    over_90: { amount: 0, count: 0, contracts: 0 },
  },
})
const aging = ref(emptyAging())
const contractQuery = reactive({ keyword: '', tenant: '', status: '', page: 1, page_size: 20 })
const billQuery = reactive({ period: '', status: '', page: 1, page_size: 20 })
const orderQuery = reactive({ keyword: '', status: '', type: '', page: 1, page_size: 20 })
const appliedOrderQuery = reactive({ keyword: '', status: '', type: '' })
const overdueOrderCount = computed(() => orders.value.filter((row) => row.overdue).length)
const agingWarning = computed(() => {
  const bucket = aging.value.buckets?.over_90
  return bucket?.count ? `超 90 天欠费 ${bucket.count} 笔，合计 ¥${fmt(bucket.amount)}` : ''
})

const contractDialog = ref(false)
const unitOptions = ref([])
const unitOptionsTotal = ref(0)
const unitOptionsLoading = ref(false)
const unitOptionsHint = computed(() => optionCapHint(unitOptions.value, unitOptionsTotal.value))
const contractForm = reactive({
  id: null, contract_no: '', tenant: '', unit_no: '', unit_id: null, area: 0,
  start_date: '', end_date: '', rent_monthly: 0, property_fee_monthly: 0,
  deposit: 0, status: 'active', note: '',
})
const billDialog = ref(false)
const billForm = reactive({ contract_id: null, contract_label: '', period: '', other_amount: 0, due_date: '' })
const paymentDialog = ref(false)
const paymentForm = reactive({ bill_id: null, label: '', amount: 0.01, paid_at: '' })
const orderDialog = ref(false)
const orderForm = reactive({
  id: null, type: 'repair', priority: 'medium', status: 'open', location: '',
  tenant: '', assignee: '', description: '', due_at: null, parent_id: null,
  equipment_id: null, contract_id: null,
})
const followupDialog = ref(false)
const followupOrder = reactive({ id: null, code: '' })
const followups = ref([])
const followupForm = reactive({ handled_at: '', content: '', result: '', satisfaction: null })

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}
function pct(v) {
  const n = Number(v)
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '—'
}
function billTag(s) {
  return { unpaid: 'danger', partial: 'warning', paid: 'success' }[s] || 'info'
}
function orderTag(s) {
  return { open: 'danger', processing: 'warning', done: 'success', closed: 'info' }[s] || 'info'
}
function todayLocal() {
  return new Date().toLocaleDateString('sv-SE')
}

async function loadSummary() {
  try {
    const { data } = await api.get('/business/operations/summary', { params: { period: billQuery.period || '' } })
    summary.value = data
  } catch { /* 拦截器已提示 */ }
}

async function loadContracts() {
  loading.value = true
  try {
    const { data } = await api.get('/business/operations/contracts', { params: contractQuery })
    contracts.value = data.items || []
    contractTotal.value = data.total ?? 0
  } catch { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

async function changeContractPage(page) {
  contractQuery.page = page
  await loadContracts()
}

function searchContracts() {
  contractQuery.page = 1
  return loadContracts()
}

async function loadExpiring() {
  try {
    const { data } = await api.get('/business/operations/contracts/expiring', {
      params: { days: expiringDays.value },
    })
    expiring.value = data || []
  } catch { /* 拦截器已提示 */ }
}

async function loadBills() {
  loading.value = true
  try {
    const { data } = await api.get('/business/operations/bills', { params: billQuery })
    bills.value = data.items || []
    billTotal.value = data.total ?? 0
  } catch { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

async function changeBillPage(page) {
  billQuery.page = page
  await loadBills()
}

function searchBills() {
  billQuery.page = 1
  return Promise.all([loadBills(), loadSummary()])
}

async function loadAging() {
  try {
    const { data } = await api.get('/business/operations/bills/aging', {
      params: { as_of: agingAsOf.value },
    })
    aging.value = data
  } catch { /* 拦截器已提示 */ }
}

async function loadOrders() {
  loading.value = true
  const params = { ...orderQuery }
  try {
    const { data } = await api.get('/business/operations/work-orders', { params })
    orders.value = data.items || []
    orderTotal.value = data.total ?? 0
    Object.assign(appliedOrderQuery, {
      status: params.status, type: params.type, keyword: params.keyword,
    })
  } catch { /* 拦截器已提示 */ } finally {
    loading.value = false
  }
}

async function changeOrderPage(page) {
  orderQuery.page = page
  await loadOrders()
}

function searchOrders() {
  orderQuery.page = 1
  return loadOrders()
}

async function loadOrderOptions(keyword = '', token = null) {
  orderOptionsLoading.value = true
  try {
    const { data } = await api.get('/business/operations/work-orders', {
      params: optionParams(keyword),
    })
    if (token !== null && !orderSearch.isCurrent(token)) return
    orderOptions.value = data.items || []
    orderOptionsTotal.value = data.total ?? orderOptions.value.length
  } catch {
    if (token !== null && !orderSearch.isCurrent(token)) return
    orderOptions.value = []
    orderOptionsTotal.value = 0
    /* 拦截器已提示 */
  } finally {
    if (token === null || orderSearch.isCurrent(token)) orderOptionsLoading.value = false
  }
}

const orderSearch = createRemoteOptionSearch((keyword, token) => loadOrderOptions(keyword, token))

function searchOrderOptions(keyword) {
  return orderSearch.search(keyword)
}

async function loadEquipmentOptions(keyword = '', token = null) {
  equipmentOptionsLoading.value = true
  try {
    const { data } = await api.get('/business/facility/equipment', {
      params: optionParams(keyword),
    })
    if (token !== null && !equipmentSearch.isCurrent(token)) return
    equipmentOptions.value = data.items || []
    equipmentOptionsTotal.value = data.total ?? equipmentOptions.value.length
  } catch {
    if (token !== null && !equipmentSearch.isCurrent(token)) return
    equipmentOptions.value = []
    equipmentOptionsTotal.value = 0
    /* 拦截器已提示 */
  } finally {
    if (token === null || equipmentSearch.isCurrent(token)) equipmentOptionsLoading.value = false
  }
}

const equipmentSearch = createRemoteOptionSearch((keyword, token) => loadEquipmentOptions(keyword, token))

function searchEquipmentOptions(keyword) {
  return equipmentSearch.search(keyword)
}

function orderOptionLabel(item) {
  if (!item) return ''
  return item.description ? `${item.code} · ${item.description}` : (item.code || `#${item.id}`)
}

function equipmentOptionLabel(item) {
  if (!item) return ''
  return item.name ? `${item.code} · ${item.name}` : (item.code || `#${item.id}`)
}

async function loadAll() {
  await loadSummary()
  await Promise.all([
    loadContracts(), loadBills(), loadOrders(), loadExpiring(), loadAging(),
    loadOrderOptions(), loadEquipmentOptions(), loadUnitOptions(),
  ])
}

async function loadUnitOptions(keyword = '', token = null) {
  unitOptionsLoading.value = true
  try {
    const { data } = await api.get('/business/leasing/units', { params: optionParams(keyword) })
    if (token !== null && !unitSearch.isCurrent(token)) return
    unitOptions.value = (data.items || []).map((u) => ({
      id: u.id,
      label: `${u.building || ''}${u.floor ? '/' + u.floor : ''} ${u.unit_no}`.trim() || `#${u.id}`,
      unit_no: u.unit_no,
    }))
    unitOptionsTotal.value = data.total ?? unitOptions.value.length
  } catch {
    if (token !== null && !unitSearch.isCurrent(token)) return
    unitOptions.value = [] // 招商模块不可用时退化为纯手填
    unitOptionsTotal.value = 0
  } finally {
    if (token === null || unitSearch.isCurrent(token)) unitOptionsLoading.value = false
  }
}

const unitSearch = createRemoteOptionSearch((keyword, token) => loadUnitOptions(keyword, token))

function searchUnitOptions(keyword) {
  return unitSearch.search(keyword)
}

function openContract(row) {
  Object.assign(contractForm, row ? {
    id: row.id, contract_no: row.contract_no, tenant: row.tenant, unit_no: row.unit_no,
    unit_id: row.unit_id ?? null,
    area: row.area, start_date: row.start_date, end_date: row.end_date,
    rent_monthly: row.rent_monthly, property_fee_monthly: row.property_fee_monthly,
    deposit: row.deposit, status: row.status, note: row.note,
  } : {
    id: null, contract_no: '', tenant: '', unit_no: '', unit_id: null, area: 0,
    start_date: '', end_date: '', rent_monthly: 0, property_fee_monthly: 0,
    deposit: 0, status: 'active', note: '',
  })
  if (row?.unit_id != null) {
    unitOptions.value = ensureOption(unitOptions.value, {
      id: row.unit_id,
      label: row.unit_no || `#${row.unit_id}`,
      unit_no: row.unit_no || '',
    })
  }
  contractDialog.value = true
}

async function saveContract() {
  if (saving.value) return
  if (!contractForm.contract_no.trim() || !contractForm.tenant.trim()) {
    ElMessage.warning('合同编号与租户不能为空')
    return
  }
  if (!contractForm.start_date || !contractForm.end_date) {
    ElMessage.warning('请填写合同起止日期')
    return
  }
  saving.value = true
  try {
    const payload = { ...contractForm }
    delete payload.id
    if (contractForm.id) {
      await api.put(`/business/operations/contracts/${contractForm.id}`, payload)
    } else {
      await api.post('/business/operations/contracts', payload)
    }
    contractDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function removeContract(row) {
  try {
    await ElMessageBox.confirm(`删除合同「${row.contract_no}」会同时删除其账单，确认？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/operations/contracts/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

function openBill(row) {
  billForm.contract_id = row.id
  billForm.contract_label = `${row.contract_no} · ${row.tenant}（月租金+物业 ¥${fmt(row.monthly_total)}）`
  billForm.period = todayLocal().slice(0, 7)
  billForm.other_amount = 0
  billForm.due_date = ''
  billDialog.value = true
}

async function generateBill() {
  if (saving.value) return
  if (!billForm.period) {
    ElMessage.warning('请选择所属期')
    return
  }
  saving.value = true
  try {
    await api.post(`/business/operations/contracts/${billForm.contract_id}/bills`, {
      period: billForm.period,
      other_amount: billForm.other_amount,
      due_date: billForm.due_date || null,
    })
    billDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

function openPayment(row) {
  paymentForm.bill_id = row.id
  paymentForm.label = `${row.period} · ${row.tenant} · 欠费 ¥${fmt(row.arrears)}`
  paymentForm.amount = Math.max(0.01, Number(row.arrears) || 0.01)
  paymentForm.paid_at = todayLocal()
  paymentDialog.value = true
}

async function payBill() {
  if (saving.value) return
  saving.value = true
  try {
    await api.post(`/business/operations/bills/${paymentForm.bill_id}/payments`, {
      amount: paymentForm.amount,
      paid_at: paymentForm.paid_at || null,
    })
    paymentDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

function openOrder(row) {
  Object.assign(orderForm, row ? {
    id: row.id, type: row.type, priority: row.priority, status: row.status,
    location: row.location, tenant: row.tenant, assignee: row.assignee, description: row.description,
    due_at: row.due_at || null, parent_id: row.parent_id ?? null,
    equipment_id: row.equipment_id ?? null, contract_id: row.contract_id ?? null,
  } : {
    id: null, type: 'repair', priority: 'medium', status: 'open', location: '',
    tenant: '', assignee: '', description: '', due_at: null, parent_id: null,
    equipment_id: null, contract_id: null,
  })
  if (row?.parent_id != null) {
    orderOptions.value = ensureOption(orderOptions.value, {
      id: row.parent_id,
      code: row.parent_code || `#${row.parent_id}`,
      description: '',
    })
  }
  if (row?.equipment_id != null) {
    equipmentOptions.value = ensureOption(equipmentOptions.value, {
      id: row.equipment_id,
      code: row.equipment_code || `#${row.equipment_id}`,
      name: '',
    })
  }
  orderDialog.value = true
}

async function saveOrder() {
  if (saving.value) return
  if (!orderForm.description.trim()) {
    ElMessage.warning('工单描述不能为空')
    return
  }
  saving.value = true
  try {
    const payload = { ...orderForm }
    delete payload.id
    if (orderForm.id) {
      await api.put(`/business/operations/work-orders/${orderForm.id}`, payload)
    } else {
      await api.post('/business/operations/work-orders', payload)
    }
    orderDialog.value = false
    await loadAll()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function removeOrder(row) {
  try {
    await ElMessageBox.confirm(`确认删除工单「${row.code}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/business/operations/work-orders/${row.id}`)
    await loadAll()
  } catch { /* 拦截器已提示 */ }
}

async function loadFollowups() {
  try {
    const { data } = await api.get(`/business/operations/work-orders/${followupOrder.id}/followups`)
    followups.value = data || []
  } catch { /* 拦截器已提示 */ }
}

async function openFollowups(row) {
  Object.assign(followupOrder, { id: row.id, code: row.code })
  Object.assign(followupForm, { handled_at: '', content: '', result: '', satisfaction: null })
  followupDialog.value = true
  await loadFollowups()
}

async function saveFollowup() {
  if (saving.value) return
  if (!followupForm.content.trim()) {
    ElMessage.warning('跟进内容不能为空')
    return
  }
  saving.value = true
  try {
    await api.post(`/business/operations/work-orders/${followupOrder.id}/followups`, {
      handled_at: followupForm.handled_at || null,
      content: followupForm.content.trim(),
      result: followupForm.result.trim(),
      satisfaction: followupForm.satisfaction,
    })
    Object.assign(followupForm, { handled_at: '', content: '', result: '', satisfaction: null })
    await loadFollowups()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

function exportFile(name) {
  window.open(`${api.defaults.baseURL}/business/operations/export/${name}`, '_blank')
  ElMessage.info('正在导出')
}

function exportOrders() {
  const params = new URLSearchParams()
  for (const key of ['status', 'type', 'keyword']) {
    if (appliedOrderQuery[key]) params.set(key, appliedOrderQuery[key])
  }
  const query = params.toString()
  window.open(`${api.defaults.baseURL}/business/operations/export/work-orders.xlsx${query ? `?${query}` : ''}`, '_blank')
  ElMessage.info('正在导出工单')
}

onMounted(loadAll)
onBeforeUnmount(() => {
  orderSearch.cancel()
  equipmentSearch.cancel()
  unitSearch.cancel()
})
</script>

<style scoped>
.kpi { margin-bottom: 8px; }
.kpi-sub { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.expire-item { margin-right: 12px; }
.aging-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.aging-bucket { display: flex; flex-direction: column; gap: 3px; padding: 8px 10px; background: var(--el-fill-color-light); border-radius: 6px; }
.aging-bucket span { font-weight: 600; }
.aging-bucket small { color: var(--el-text-color-secondary); }
</style>
