<template>
  <el-config-provider :locale="zhCn">
    <AppLayout />
  </el-config-provider>
</template>

<script setup>
import AppLayout from './components/AppLayout.vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>
