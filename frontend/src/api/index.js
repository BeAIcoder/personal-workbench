import axios from 'axios'
import { ElMessage } from 'element-plus'

// SSE（fetch）与 axios 共用同一个 baseURL 来源
export const API_BASE = import.meta.env.VITE_API_BASE || '/api'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 10000,
})

function toMessage(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    const field = (first.loc || []).slice(1).join('.')
    return `${field ? field + '：' : ''}${first.msg}`
  }
  if (error?.code === 'ECONNABORTED') return '请求超时，请确认后端服务已启动'
  return '网络异常，请确认后端服务已启动（默认 http://127.0.0.1:8000）'
}

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    ElMessage.error(toMessage(error))
    return Promise.reject(error)
  },
)

export const taskApi = {
  list: (params) => api.get('/tasks', { params }),
  get: (id) => api.get(`/tasks/${id}`),
  create: (data) => api.post('/tasks', data),
  update: (id, data) => api.put(`/tasks/${id}`, data),
  remove: (id) => api.delete(`/tasks/${id}`),
}

export const scheduleApi = {
  list: (params) => api.get('/schedules', { params }),
  get: (id) => api.get(`/schedules/${id}`),
  create: (data) => api.post('/schedules', data),
  update: (id, data) => api.put(`/schedules/${id}`, data),
  remove: (id) => api.delete(`/schedules/${id}`),
}

// 定时任务：到点自动执行 Agent 任务，结果写入笔记
export const jobApi = {
  list: () => api.get('/jobs'),
  create: (data) => api.post('/jobs', data),
  update: (id, data) => api.put(`/jobs/${id}`, data),
  remove: (id) => api.delete(`/jobs/${id}`),
  runNow: (id) => api.post(`/jobs/${id}/run`, {}, { timeout: 120000 }),
}

export const noteApi = {
  list: (params) => api.get('/notes', { params }),
  tags: () => api.get('/notes/tags'),
  get: (id) => api.get(`/notes/${id}`),
  create: (data) => api.post('/notes', data),
  update: (id, data) => api.put(`/notes/${id}`, data),
  remove: (id) => api.delete(`/notes/${id}`),
}

export const dashboardApi = {
  summary: () => api.get('/dashboard/summary'),
}

export const searchApi = {
  query: (q) => api.get('/search', { params: { q } }),
}

// Agent 团队：对话走 SSE 流式（fetch），其余接口用 axios
export const assistantApi = {
  team: () => api.get('/assistant/team'),
  history: (sessionId) => api.get(`/assistant/history/${sessionId}`),

  sessions: {
    list: () => api.get('/assistant/sessions'),
    create: (data) => api.post('/assistant/sessions', data),
    rename: (sessionId, title) => api.put(`/assistant/sessions/${sessionId}`, { session_id: sessionId, title }),
    remove: (sessionId) => api.delete(`/assistant/sessions/${sessionId}`),
  },

  config: {
    get: () => api.get('/assistant/config'),
    saveGlobals: (data) => api.put('/assistant/config', data),
    test: (data) => api.post('/assistant/config/test', data, { timeout: 90000 }),
    discoverSkills: (dir) => api.get('/assistant/skills/discover', { params: { dir } }),
    discoverPlugins: (dir) => api.get('/assistant/plugins/discover', { params: { dir } }),
    upload: (sessionId, file) => {
      const fd = new FormData()
      fd.append('session_id', sessionId)
      fd.append('file', file)
      return api.post('/assistant/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 })
    },
  },

  models: {
    list: () => api.get('/assistant/models'),
  },

  agents: {
    list: () => api.get('/assistant/agents'),
    create: (data) => api.post('/assistant/agents', data),
    update: (id, data) => api.put(`/assistant/agents/${id}`, data),
    remove: (id) => api.delete(`/assistant/agents/${id}`),
    routeTest: (text) => api.post('/assistant/agents/route-test', { text }),
    reset: () => api.post('/assistant/agents/reset'),
  },

  providers: {
    save: (data) => api.post('/assistant/providers', data),
    remove: (id) => api.delete(`/assistant/providers/${id}`),
    saveModel: (providerId, data) => api.post(`/assistant/providers/${providerId}/models`, data),
    removeModel: (providerId, modelId) => api.delete(`/assistant/providers/${providerId}/models/${modelId}`),
    activate: (modelId) => api.post(`/assistant/models/${modelId}/activate`),
  },
}

export default api
