<template>
  <span class="template-links">
    <span class="tpl-label">下载模板：</span>
    <el-link
      v-for="t in files"
      :key="t.file"
      type="primary"
      :underline="false"
      class="tpl-link"
      @click="downloadTemplate(t.file)"
    >{{ t.label }}</el-link>
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { DATA_TEMPLATES, downloadTemplate } from '../utils/dataTemplates'

// type 为 DATA_TEMPLATES 的键（vault/personal/expense/cashflow/pos/bip），
// 也可直接传 files=[{file,label}] 覆盖默认清单
const props = defineProps({
  type: { type: String, default: '' },
  files: { type: Array, default: null },
})

const files = computed(() => props.files || DATA_TEMPLATES[props.type] || [])
</script>

<style scoped>
.template-links {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: 8px;
  white-space: nowrap;
}
.tpl-label {
  margin-right: 2px;
}
.tpl-link {
  font-size: 12px;
  margin-right: 10px;
}
</style>
