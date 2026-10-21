<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>快速记账</span>
          <el-button size="small" link type="primary" @click="manageVisible = true">账户 / 分类管理</el-button>
        </div>
      </template>
      <el-form :model="form" label-width="72px" style="max-width: 560px">
        <el-form-item label="类型">
          <el-radio-group v-model="form.kind">
            <el-radio-button value="expense">支出</el-radio-button>
            <el-radio-button value="income">收入</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="金额">
          <el-input-number v-model="form.amount" :min="0.01" :precision="2" :controls="false" style="width: 180px" />
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" style="width: 180px" />
        </el-form-item>
        <el-form-item label="账户">
          <el-select v-model="form.account_id" style="width: 220px">
            <el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="form.category_id" style="width: 220px">
            <el-option v-for="c in filteredCategories" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" maxlength="100" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="submit">记一笔</el-button>
          <el-button link type="primary" @click="$router.push({ name: 'finance-ledger-personal-transactions' })">查看流水</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header><span>本月流水（{{ month }}）</span></template>
      <el-table :data="todayItems" size="small" empty-text="本月还没有记账">
        <el-table-column label="类型" width="80">
          <template #default="{ row }">
            <el-tag :type="row.kind === 'income' ? 'success' : 'danger'" size="small">
              {{ row.kind === 'income' ? '收入' : '支出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="category_name" label="分类" min-width="120" />
        <el-table-column prop="account_name" label="账户" min-width="100" />
        <el-table-column label="金额" width="120" align="right">
          <template #default="{ row }">
            <span :class="row.kind === 'income' ? 'income' : 'expense'">
              {{ row.kind === 'income' ? '+' : '-' }}{{ row.amount?.toFixed(2) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
      </el-table>
    </el-card>

    <el-drawer v-model="manageVisible" title="账户 / 分类管理" size="420px">
      <h4>账户</h4>
      <div v-for="a in accounts" :key="a.id" class="manage-row">
        <span>{{ a.name }}</span>
        <el-button size="small" link type="danger" @click="deleteAccount(a)">删除</el-button>
      </div>
      <el-input v-model="newAccount" placeholder="新账户名" size="small" style="width: 200px; margin-top: 8px">
        <template #append><el-button size="small" @click="addAccount">添加</el-button></template>
      </el-input>
      <h4 style="margin-top: 24px">分类</h4>
      <div v-for="c in categories" :key="c.id" class="manage-row">
        <el-tag :type="c.kind === 'income' ? 'success' : 'info'" size="small">{{ c.kind === 'income' ? '收' : '支' }}</el-tag>
        <span style="flex: 1">{{ c.parent_id ? '　└ ' : '' }}{{ c.name }}</span>
        <el-button size="small" link type="danger" @click="deleteCategory(c)">删除</el-button>
      </div>
      <div class="add-category">
        <el-select v-model="newCategory.kind" size="small" style="width: 88px">
          <el-option value="expense" label="支出" />
          <el-option value="income" label="收入" />
        </el-select>
        <el-input v-model="newCategory.name" placeholder="新分类名" size="small" style="width: 180px" />
        <el-button size="small" type="primary" @click="addCategory">添加</el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'
import { localMonth, localToday } from '../../_shared/dates'
import { categoryMatchesKind } from '../utils'

const loading = ref(false)
const submitting = ref(false)
const accounts = ref([])
const categories = ref([])
const todayItems = ref([])
const manageVisible = ref(false)
const newAccount = ref('')
const newCategory = reactive({ kind: 'expense', name: '' })

const today = localToday()
const month = localMonth()
const form = reactive({
  kind: 'expense',
  amount: undefined,
  date: today,
  account_id: null,
  category_id: null,
  note: '',
})

const filteredCategories = computed(() => categories.value.filter((c) => c.kind === form.kind))

// 切换 收入/支出 时清理残留的旧分类：否则旧分类与交易类型不一致，提交会被后端 400 拒绝
watch(
  () => form.kind,
  () => {
    const cat = categories.value.find((c) => c.id === form.category_id)
    if (form.category_id && !categoryMatchesKind(cat, form.kind)) form.category_id = null
  }
)

async function load() {
  loading.value = true
  try {
    const [acc, cat, tx] = await Promise.all([
      api.get('/finance/ledger/personal/accounts'),
      api.get('/finance/ledger/personal/categories'),
      api.get('/finance/ledger/personal/transactions', { params: { month, page_size: 100 } }),
    ])
    accounts.value = acc.data
    categories.value = cat.data
    todayItems.value = tx.data.items
    if (!form.account_id && acc.data.length) form.account_id = acc.data[0].id
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!form.amount || !form.account_id || !form.category_id) {
    ElMessage.warning('请填写金额、账户和分类')
    return
  }
  submitting.value = true
  try {
    await api.post('/finance/ledger/personal/transactions', {
      kind: form.kind,
      amount: form.amount,
      occurred_on: form.date,
      account_id: form.account_id,
      category_id: form.category_id,
      note: form.note,
    })
    ElMessage.success('已记账')
    form.amount = undefined
    form.note = ''
    await load()
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    submitting.value = false
  }
}

async function addAccount() {
  const name = newAccount.value.trim()
  if (!name) return
  try {
    await api.post('/finance/ledger/personal/accounts', { name })
    newAccount.value = ''
    await load()
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function deleteAccount(a) {
  try {
    await api.delete(`/finance/ledger/personal/accounts/${a.id}`)
    await load()
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function addCategory() {
  const name = newCategory.name.trim()
  if (!name) return
  try {
    await api.post('/finance/ledger/personal/categories', { kind: newCategory.kind, name })
    newCategory.name = ''
    await load()
  } catch {
    /* 拦截器已统一提示 */
  }
}

async function deleteCategory(c) {
  try {
    await api.delete(`/finance/ledger/personal/categories/${c.id}`)
    await load()
  } catch {
    /* 拦截器已统一提示 */
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

.income {
  color: #67c23a;
}

.expense {
  color: #f56c6c;
}

.manage-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.add-category {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
</style>
