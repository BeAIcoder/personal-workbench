import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式下 5173 端口的前端会把 /api 请求代理到 8000 端口的后端
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // vendor 拆包：vue 与 element-plus 必须同 chunk——EP 初始化与 vue 存在跨包初始化顺序耦合，
        // 分开拆会触发「Cannot access before initialization」运行时错误（本机实测踩坑，冒烟抓回）。
        // echarts / ag-grid 是单向依赖 vue 的叶子库，可安全独立。
        manualChunks: {
          'vendor-core': ['vue', 'vue-router', 'axios', 'dayjs', 'markdown-it', 'element-plus', '@element-plus/icons-vue'],
          'vendor-echarts': ['echarts'],
          'vendor-ag-grid': ['ag-grid-community', 'ag-grid-vue3'],
        },
      },
    },
  },
  test: {
    environment: 'node',
  },
})
