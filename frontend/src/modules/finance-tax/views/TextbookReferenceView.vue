<template>
  <div class="page" v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>教材考纲对照</span>
          <el-radio-group v-model="tab" size="small">
            <el-radio-button value="list">条目</el-radio-button>
            <el-radio-button value="mappings">映射关系</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-table :data="items" size="small" empty-text="暂无数据">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="expand-content">{{ JSON.stringify(row, null, 2) }}</div>
          </template>
        </el-table-column>
        <el-table-column v-for="c in columns" :key="c" :prop="c" :label="c" min-width="140" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import api from '../../../api'

const loading = ref(false)
const tab = ref('list')
const items = ref([])
const columns = ref([])

async function load() {
  loading.value = true
  try {
    const path = tab.value === 'mappings' ? '/finance/tax/textbook-reference/mappings' : '/finance/tax/textbook-reference/'
    const { data } = await api.get(path)
    items.value = data
    const first = data[0]
    columns.value = first ? Object.keys(first).filter((k) => typeof first[k] !== 'object') : []
  } catch {
    items.value = []
    columns.value = []
  } finally {
    loading.value = false
  }
}

watch(tab, load)
onMounted(load)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.expand-content {
  white-space: pre-wrap;
  font-family: monospace;
  font-size: 12px;
  color: #606266;
}
</style>
