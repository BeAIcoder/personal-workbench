<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header><span>政策库</span></template>
      <el-table :data="items" size="small">
        <el-table-column prop="policy_number" label="文号" min-width="180" show-overflow-tooltip />
        <el-table-column prop="title" label="政策名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="policy_type" label="类型" width="110" />
        <el-table-column prop="version" label="版本" width="70" align="center" />
        <el-table-column label="生效日期" width="120">
          <template #default="{ row }">{{ fmt(row.effective_date) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="toggle(row)">
              {{ row.is_active ? '停用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import api from '../../../api'

const loading = ref(false)
const items = ref([])

const fmt = (v) => (v ? String(v).slice(0, 10) : '')

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/finance/tax/policies/')
    items.value = data.items || data
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

async function toggle(row) {
  try {
    if (row.is_active) {
      await api.post(`/finance/tax/policies/${row.id}/deactivate`)
    } else {
      await api.post(`/finance/tax/policies/${row.id}/activate`)
    }
    await load()
  } catch {
    /* 拦截器已统一提示 */
  }
}

onMounted(load)
</script>
