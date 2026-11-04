import { createApp } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import { ElLoading } from 'element-plus'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'

// 菜单/统计卡经 <component :is="字符串名"> 渲染的图标：精确注册表。
// 模板内 PascalCase 图标标签由 vite.config 的 unplugin resolver 构建期解析，不在此列。
import {
  Aim, Calendar, ChatDotRound, Clock, Coin, Collection, Connection, CreditCard,
  DataAnalysis, DataBoard, DataLine, Document, Finished, Folder, Histogram, Monitor,
  Notebook, Odometer, OfficeBuilding, Promotion, Reading, Setting, Shop, Stamp,
  Tickets, Timer, Tools, TrendCharts, User, UserFilled, Warning,
} from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import { debugApi } from './api'
import { installChunkRecovery } from './utils/chunkRecovery'
import './style.css'

dayjs.locale('zh-cn')

const app = createApp(App)
installChunkRecovery(window, router)
app.use(router)
// EP 按需引入后不再 app.use(ElementPlus)：locale 由 App.vue 的 el-config-provider 提供；
// v-loading 指令需单独注册；字符串名图标只注册实际用到的 31 个。
app.use(ElLoading)
const STRING_ICONS = {
  Aim, Calendar, ChatDotRound, Clock, Coin, Collection, Connection, CreditCard,
  DataAnalysis, DataBoard, DataLine, Document, Finished, Folder, Histogram, Monitor,
  Notebook, Odometer, OfficeBuilding, Promotion, Reading, Setting, Shop, Stamp,
  Tickets, Timer, Tools, TrendCharts, User, UserFilled, Warning,
}
for (const [name, component] of Object.entries(STRING_ICONS)) {
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
  // 自定义 errorHandler 会连带取消 Vue 默认的控制台输出：开发态补回一份，
  // 否则 devtools 里看不到渲染错误，E2E 探针的 console_errors 也采不到（生产构建 DEV=false，不打印）。
  if (import.meta.env.DEV) console.error('[vue:error]', info, err)
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
