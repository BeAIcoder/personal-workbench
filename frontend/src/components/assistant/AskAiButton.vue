<template>
  <el-tooltip content="用 AI 分析本页数据（跳转 AI 助手并自动提问）" placement="top">
    <el-button
      size="small"
      text
      type="primary"
      :icon="MagicStick"
      :data-testid="'ask-ai'"
      @click="ask"
    >问AI</el-button>
  </el-tooltip>
</template>

<script setup>
import { MagicStick } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { buildAssistantAutoRoute } from '../../utils/assistantEntry'

const props = defineProps({
  prompt: { type: String, required: true },
})

const router = useRouter()
const ask = () => router.push(buildAssistantAutoRoute(props.prompt))
</script>
