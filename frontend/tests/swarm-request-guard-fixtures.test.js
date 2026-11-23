import { describe, expect, it } from 'vitest'
import { scanUnguardedRequests } from './no-unhandled-request.test.js'

// 请求守卫的正/反夹具。守卫口径见 no-unhandled-request.test.js 顶部注释：
// 每个 await 请求要么自带 .catch()，要么被带 catch 的 try 词法包住；
// 不 await 的请求链（.then / fire-and-forget）也必须有 .catch 兜底。
// 正夹具 = 必须报出（旧「缩进启发式」看不到的盲区）；反夹具 = 不得误报。
// 只覆盖本项目写法（标准 ES 模块 + script setup，无 TS），不伪装完整 JS 解析器。

const REASON_FINALLY = 'try/finally 无 catch'
const REASON_NO_TRY = '无 try/catch'
const REASON_THEN = 'then 无 catch'
const REASON_DISCARDED = '未处理的请求调用'

function reasons(source, filename = 'fixture.js') {
  return scanUnguardedRequests(source, filename).map((h) => h.reason)
}

// ---- 正夹具：以下写法都是未捕获 rejection，必须报出 ----

const P_TRY_FINALLY = `
const load = async () => {
  try {
    const { data } = await api.list()
    rows.value = data
  } finally {
    loading.value = false
  }
}
`

const P_TRY_FINALLY_INLINE = `
async function save() {
  saving.value = true
  try { await api.save(form) } finally { saving.value = false }
}
`

const P_NESTED_ARROW = `
watch(query, async (q) => {
  try {
    const { data } = await api.search(q)
    hits.value = data
  } finally {
    searching.value = false
  }
})
`

const P_NESTED_NO_TRY = `
const handler = async () => {
  await api.remove(row.id)
}
`

const P_DEEP_NESTED = `
rows.forEach(async (row) => {
  await api.update(row.id, { done: true })
})
`

const P_AWAIT_IN_CATCH = `
async function refresh() {
  try {
    await api.list()
  } catch {
    await api.audit()
  }
}
`

const P_AWAIT_IN_FINALLY = `
async function refresh() {
  try {
    await api.list()
  } finally {
    await api.log()
  }
}
`

const P_PROMISE_ALL = `
async function load() {
  const [acc, cat] = await Promise.all([
    api.get('/accounts'),
    api.get('/categories'),
  ])
}
`

const P_FETCH_NO_TRY = `
const send = async () => {
  const resp = await fetch('/chat/stream')
  return resp
}
`

const P_NESTED_TRY_FINALLY = `
async function run() {
  try {
    try {
      await api.a()
    } finally {
      cleanup()
    }
  } finally {
    done()
  }
}
`

const P_UNRELATED_CATCH = `
const load = async () => {
  await api.list()
  analytics.track('loaded').catch(() => {})
}
`

// .then 链丢弃结果且没有 .catch：回调抛错或请求失败都会变成未捕获 rejection
const P_THEN_NO_CATCH = `
onMounted(() => {
  api.get('/health').then(({ data }) => {
    version.value = data.version
  })
})
`

const P_THEN_NO_CATCH_MULTILINE = `
openDialog()
api
  .get('/settings')
  .then(({ data }) => { home.value = data.home })
`

// 完全不接的请求调用（fire-and-forget）：rejection 无人处理
const P_FIRE_AND_FORGET = `
function fireAndForget() {
  api.post('/audit').catch(() => {})
  api.post('/audit-2', payload)
}
`

const P_FIRE_AND_FORGET_VOID = `
const refresh = () => {
  void api.post('/audit', payload)
}
`

const P_FIRE_AND_FORGET_FETCH = `
function warmup() {
  fetch('/api/health')
}
`

// 领域方法（*Api 根下的任意方法）同样是请求，不能因不是 get/post 就漏报
const P_API_DOMAIN_THEN = `
onMounted(() => {
  knowledgeApi.docs({ q: 'x' }).then(({ data }) => {
    rows.value = data.items
  })
})
`

const P_API_DOMAIN_BARE = `
function refresh(id) {
  assistantApi.history(id)
}
`

// void 包住的 .then 链同样会丢弃 rejection
const P_VOID_THEN = `
function prefetch() {
  void api.get('/health').then(({ data }) => { version.value = data.version })
}
`

const P_VUE_BLIND_SPOT = `<template>
  <el-button @click="load">刷新</el-button>
</template>

<script setup>
const load = async () => {
  try {
    const { data } = await api.list()
    rows.value = data
  } finally {
    loading.value = false
  }
}
</script>
`

const POSITIVE = [
  ['try/finally 无 catch（4 格缩进盲区）', P_TRY_FINALLY, [REASON_FINALLY]],
  ['try/finally 无 catch（紧凑单行）', P_TRY_FINALLY_INLINE, [REASON_FINALLY]],
  ['嵌套箭头函数里的 try/finally 盲区', P_NESTED_ARROW, [REASON_FINALLY]],
  ['嵌套函数完全没有任何 try', P_NESTED_NO_TRY, [REASON_NO_TRY]],
  ['forEach 异步回调里的请求', P_DEEP_NESTED, [REASON_NO_TRY]],
  ['catch 块内的 await 仍无保护（try 有 catch，块内那个已接住）', P_AWAIT_IN_CATCH, [REASON_NO_TRY]],
  ['finally 块内的 await 仍无保护', P_AWAIT_IN_FINALLY, [REASON_FINALLY, REASON_NO_TRY]],
  ['Promise.all 包住请求且无 try/catch', P_PROMISE_ALL, [REASON_NO_TRY]],
  ['fetch 流式请求无 try/catch', P_FETCH_NO_TRY, [REASON_NO_TRY]],
  ['双层 try/finally 都无 catch', P_NESTED_TRY_FINALLY, [REASON_FINALLY]],
  ['无关语句上的 .catch 不算保护', P_UNRELATED_CATCH, [REASON_NO_TRY]],
  ['.then 丢弃结果且无 .catch', P_THEN_NO_CATCH, [REASON_THEN]],
  ['.then 多行链无 .catch', P_THEN_NO_CATCH_MULTILINE, [REASON_THEN]],
  ['fire-and-forget 请求调用（无 await 无 catch）', P_FIRE_AND_FORGET, [REASON_DISCARDED]],
  ['void 包住的 fire-and-forget 请求', P_FIRE_AND_FORGET_VOID, [REASON_DISCARDED]],
  ['fire-and-forget fetch', P_FIRE_AND_FORGET_FETCH, [REASON_DISCARDED]],
  ['领域方法 .then 无 catch', P_API_DOMAIN_THEN, [REASON_THEN]],
  ['领域方法 fire-and-forget', P_API_DOMAIN_BARE, [REASON_DISCARDED]],
  ['void 包住的 .then 无 catch', P_VOID_THEN, [REASON_THEN]],
  ['.vue script setup 端到端（行号映射）', P_VUE_BLIND_SPOT, [REASON_FINALLY], 'fixture.vue'],
]

// ---- 反夹具：以下写法已有保护或不在口径内，不得误报 ----

const N_CATCH_ON_PROMISE = `
const load = async () => {
  const { data } = await api.list().catch(() => ({ data: [] }))
}
`

const N_CATCH_MULTILINE = `
const load = async () => {
  const { data } = await api
    .list()
    .catch(() => ({ data: [] }))
}
`

const N_TRY_CATCH = `
const load = async () => {
  try {
    const { data } = await api.list()
  } catch (e) {
    ElMessage.error(e.message)
  }
}
`

const N_TRY_CATCH_FINALLY = `
const load = async () => {
  try {
    await api.list()
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}
`

const N_OUTER_CATCH_PROTECTS = `
const load = async () => {
  try {
    try {
      await api.list()
    } finally {
      loading.value = false
    }
  } catch (e) {
    ElMessage.error(String(e))
  }
}
`

const N_NON_REQUEST_AWAITS = `
const submit = async () => {
  const ok = await formRef.value.validate().catch(() => false)
  await nextTick()
  await ElMessageBox.confirm('确认删除？')
  await vault.get(apiKey)
  await loadLocal()
}
`

const N_NO_AWAIT_WITH_CATCH = `
function fireAndForget() {
  api.get('/audit').catch(() => {})
  void knowledgeApi.docs().catch(() => {})
}
`

const N_THEN_TWO_HANDLERS = `
onMounted(() => {
  api.get('/health').then(
    ({ data }) => { version.value = data.version },
    () => { failed.value = true },
  )
})
`

const N_API_DOMAIN_WITH_CATCH = `
function load() {
  knowledgeApi.docs({ q: 'x' }).then(({ data }) => { rows.value = data.items }).catch(() => {})
  assistantApi.history(1).catch(() => {})
}
`

const N_THEN_WITH_CATCH = `
api.get('/x').then(cb).catch(() => {})
api
  .get('/y')
  .then(cb)
  .catch(() => {})
`

const N_THEN_ASSIGNED = `
function load() {
  return api.get('/x').then((r) => r.data)
}
`

const N_NON_REQUEST_STATEMENT = `
function setup() {
  api.interceptors.response.use((r) => r)
  api.defaults.baseURL = '/api'
  console.log('done')
}
`

const N_FETCH_TRY_CATCH = `
const send = async () => {
  try {
    const resp = await fetch(\`\${API_BASE}/chat/stream\`, { method: 'POST' })
    if (!resp.ok) throw new Error(\`HTTP \${resp.status}\`)
  } catch (e) {
    error.value = e.message
  }
}
`

const N_NESTED_ARROW_IN_TRY = `
onMounted(async () => {
  try {
    const run = async () => {
      await api.warmup()
    }
    await run()
  } catch (e) {
    /* 已处理 */
  }
})
`

const N_DYNAMIC_IMPORT = `
const load = async () => {
  const mod = await import('./api/helper')
  mod.run()
}
`

const N_VUE_HANDLED = `<template>
  <el-button @click="save">保存</el-button>
</template>

<script setup>
const save = async () => {
  try {
    await api.save(form)
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}
</script>
`

const NEGATIVE = [
  ['await 的 promise 自带 .catch', N_CATCH_ON_PROMISE],
  ['.catch 换行书写也算保护', N_CATCH_MULTILINE],
  ['try/catch 接住', N_TRY_CATCH],
  ['try/catch/finally 接住', N_TRY_CATCH_FINALLY],
  ['内层 try/finally 被外层 try/catch 词法接住', N_OUTER_CATCH_PROTECTS],
  ['非请求 await（validate/nextTick/ElMessageBox/本地函数）', N_NON_REQUEST_AWAITS],
  ['无 await 但有 .catch 的请求（含 void）', N_NO_AWAIT_WITH_CATCH],
  ['.then(ok, err) 自带 rejection 处理器不算漏网', N_THEN_TWO_HANDLERS],
  ['领域方法 .then().catch() / 裸调用 .catch()', N_API_DOMAIN_WITH_CATCH],
  ['.then 链尾有 .catch（单行/多行）', N_THEN_WITH_CATCH],
  ['return 交给调用方的 .then 链', N_THEN_ASSIGNED],
  ['拦截器注册与 defaults 访问不是请求调用', N_NON_REQUEST_STATEMENT],
  ['fetch 请求在 try/catch 内', N_FETCH_TRY_CATCH],
  ['嵌套箭头函数被外层 try/catch 词法接住', N_NESTED_ARROW_IN_TRY],
  ['await import() 动态导入不算请求', N_DYNAMIC_IMPORT],
  ['.vue 事件处理器已有 try/catch 处理', N_VUE_HANDLED, 'fixture.vue'],
]

describe('请求守卫正夹具：盲区写法必须被报出', () => {
  it.each(POSITIVE)('%s', (_name, source, expected, filename = 'fixture.js') => {
    expect(reasons(source, filename)).toEqual(expected)
  })
})

describe('请求守卫反夹具：已有处理的写法不得误报', () => {
  it.each(NEGATIVE)('%s', (_name, source, filename = 'fixture.js') => {
    expect(reasons(source, filename)).toEqual([])
  })
})

describe('请求守卫行号自检', () => {
  // 行号自检只针对 await 类夹具（新夹具不包含 await，单独断言）
  const AWAIT_POSITIVE = POSITIVE.filter(([, source]) => /\bawait\b/.test(source))

  it.each(AWAIT_POSITIVE)('%s：报出的行都落在 await 语句上', (_name, source, _expected, filename = 'fixture.js') => {
    const lines = source.split('\n')
    for (const hit of scanUnguardedRequests(source, filename)) {
      expect(lines[hit.line - 1]).toContain('await')
    }
  })

  it('.then 无 catch 与 fire-and-forget 报在触发语句行', () => {
    const thenLines = P_THEN_NO_CATCH_MULTILINE.split('\n')
    const thenHit = scanUnguardedRequests(P_THEN_NO_CATCH_MULTILINE).find((h) => h.reason === REASON_THEN)
    expect(thenLines[thenHit.line - 1].trim()).toContain('.then(')

    const bareLines = P_FIRE_AND_FORGET.split('\n')
    const bareHit = scanUnguardedRequests(P_FIRE_AND_FORGET).find((h) => h.reason === REASON_DISCARDED)
    expect(bareLines[bareHit.line - 1]).toContain("api.post('/audit-2'")
  })

  it('.vue 夹具报出原始文件行号', () => {
    const hits = scanUnguardedRequests(P_VUE_BLIND_SPOT, 'fixture.vue')
    expect(hits).toEqual([{ line: 8, reason: REASON_FINALLY, snippet: expect.any(String) }])
    expect(P_VUE_BLIND_SPOT.split('\n')[7]).toContain('await api.list()')
  })
})
