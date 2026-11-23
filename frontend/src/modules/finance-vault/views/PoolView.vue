<template>
  <div class="page">
    <HelpLink section="6.1" />
    <UsageHelp :page="tab === 'sql' ? 'query' : 'list'" />
    <el-tabs v-model="tab" class="pool-tabs">
      <el-tab-pane label="数据池" name="tables">
        <TablesPanel @jump-to-analyze="onJumpToAnalyze" />
      </el-tab-pane>
      <el-tab-pane label="整合分析" name="analyze" lazy>
        <AnalyzePanel :preset-table="presetTable" />
      </el-tab-pane>
      <el-tab-pane label="SQL 查询" name="sql" lazy>
        <SqlPanel :preset-table="presetTable" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HelpLink from '../../../components/HelpLink.vue'
import UsageHelp from '../UsageHelp.vue'
import { resolveTab } from '../poolLogic'
import TablesPanel from './parts/TablesPanel.vue'
import AnalyzePanel from './parts/AnalyzePanel.vue'
import SqlPanel from './parts/SqlPanel.vue'

const route = useRoute()
const router = useRouter()

// URL ?table= 预选表名：切页签/跳转时随 URL 传递
const presetTable = computed(() => {
  const t = route.query.table
  return typeof t === 'string' ? t : ''
})

const tab = computed({
  get: () => resolveTab({ queryTab: route.query.tab, metaTab: route.meta && route.meta.tab }),
  set: (v) => {
    // 始终写回 URL：/finance/vault/query 的 meta.tab=sql 只做兜底，
    // 若切到「数据池」时把 tab 从 query 里删掉，会被 meta 拉回 SQL。
    if (String(route.query.tab || '') !== v) {
      router.replace({ query: { ...route.query, tab: v } })
    }
  },
})

// 表清单页「分析」按钮：切到整合分析页签并带上预选表名
function onJumpToAnalyze(table) {
  router.replace({ query: { tab: 'analyze', table } })
}
</script>
