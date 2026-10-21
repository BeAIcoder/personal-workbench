<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>科目发生额汇总</span>
          <div class="filters">
            <el-input v-model="period" placeholder="期间 YYYY-MM" size="small" style="width: 140px" />
            <el-button size="small" type="primary" @click="load">汇总</el-button>
          </div>
        </div>
      </template>
      <el-table :data="data.items" size="small" border empty-text="选择期间后点汇总">
        <el-table-column prop="subject_code" label="科目编码" width="120" />
        <el-table-column prop="subject_name" label="科目名称" min-width="180" />
        <el-table-column prop="entry_count" label="笔数" width="90" align="right" />
        <el-table-column label="借方合计" width="140" align="right">
          <template #default="{ row }">{{ row.debit_total?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="贷方合计" width="140" align="right">
          <template #default="{ row }">{{ row.credit_total?.toFixed(2) }}</template>
        </el-table-column>
      </el-table>
      <div v-if="data.subject_count" class="total-line">
        共 {{ data.subject_count }} 个科目 ｜ 借方合计 {{ data.debit_total?.toFixed(2) }} ｜ 贷方合计 {{ data.credit_total?.toFixed(2) }}
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../../../api'
import { localMonth } from '../../_shared/dates'

const loading = ref(false)
const period = ref(localMonth())
const data = reactive({ period: '', items: [], subject_count: 0, debit_total: 0, credit_total: 0 })

async function load() {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(period.value)) {
    ElMessage.warning('期间格式应为 YYYY-MM')
    return
  }
  loading.value = true
  try {
    const { data: resp } = await api.get('/finance/ledger/bip/balances/summary', { params: { period: period.value } })
    Object.assign(data, resp)
  } catch {
    data.items = []
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
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

.filters {
  display: flex;
  gap: 8px;
  align-items: center;
}

.total-line {
  margin-top: 12px;
  color: #606266;
}
</style>
