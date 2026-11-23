<template>
  <div class="page">
    <HelpLink section="4.1" />
    <el-card shadow="never">
      <el-tabs v-model="tab">
        <el-tab-pane label="制度与手册" name="docs">
          <DocLibrary ref="library" @open-doc="openDoc" />
        </el-tab-pane>
        <el-tab-pane label="知识检索" name="search">
          <KnowledgeSearch @open-doc="openDoc" />
        </el-tab-pane>
        <el-tab-pane label="长期记忆" name="memory">
          <MemoryPanel />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <DocDetailDrawer
      v-model="drawer.visible"
      :doc-id="drawer.docId"
      :chunk-id="drawer.chunkId"
      @changed="refreshLibrary"
    />
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import HelpLink from '../components/HelpLink.vue'
import DocDetailDrawer from '../components/knowledge/DocDetailDrawer.vue'
import DocLibrary from '../components/knowledge/DocLibrary.vue'
import KnowledgeSearch from '../components/knowledge/KnowledgeSearch.vue'
import MemoryPanel from '../components/knowledge/MemoryPanel.vue'

const tab = ref('docs')
const library = ref(null)
const drawer = reactive({ visible: false, docId: null, chunkId: null })

function openDoc({ docId, chunkId }) {
  drawer.docId = docId
  drawer.chunkId = chunkId || null
  drawer.visible = true
}

function refreshLibrary() {
  library.value?.reload?.()
}
</script>
