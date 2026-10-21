import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import * as Icons from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'

import App from './App.vue'
import router from './router'
import { debugApi } from './api'
import './style.css'

dayjs.locale('zh-cn')

const app = createApp(App)
app.use(router)
app.use(ElementPlus, { locale: zhCn })
for (const [name, component] of Object.entries(Icons)) {
  app.component(name, component)
}

// ---- 全局报错采集 → /api/debug/client-errors ----
// 静默失败（裸 axios，不走拦截器）、同消息 5 秒去重、绝不阻塞主流程
const _reported = new Map()

function reportClientError(message, stack = '') {
  try {
    const key = String(message || '')
    const now = Date.now()
    if (now - (_reported.get(key) || 0) < 5000) return
    _reported.set(key, now)
    if (_reported.size > 200) {
      // 防 Map 无限增长：淘汰 60s 前的记录
      for (const [k, t] of _reported) {
        if (now - t > 60000) _reported.delete(k)
      }
    }
    debugApi.clientErrors.report({
      ts: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      page: router.currentRoute.value?.path || '',
      message: key.slice(0, 2000),
      stack: String(stack || '').slice(0, 4000),
      ua: navigator.userAgent || '',
    }).catch(() => {})
  } catch {
    /* 采集器自身绝不抛错 */
  }
}

app.config.errorHandler = (err, _instance, info) => {
  reportClientError(err?.message || 'Vue 渲染错误', err?.stack || info || '')
}
window.addEventListener('error', (e) => {
  reportClientError(e.message || 'window error', e.error?.stack || '')
})
window.addEventListener('unhandledrejection', (e) => {
  const reason = e.reason
  reportClientError(reason?.message || String(reason), reason?.stack || '')
})

app.mount('#app')
