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
  },
  test: {
    environment: 'node',
  },
})
