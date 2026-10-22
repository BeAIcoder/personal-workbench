import { describe, expect, it } from 'vitest'
import { scanUnguardedRequests } from './no-unhandled-request.test.js'

// 请求守卫的正/反夹具。守卫口径见 no-unhandled-request.test.js 顶部注释：
// 每个 await 请求要么自带 .catch()，要么被带 catch 的 try 词法包住。
// 正夹具 = 必须报出（旧「缩进启发式」看不到的盲区）；反夹具 = 不得误报。
// 只覆盖本项目写法（标准 ES 模块 + script setup，无 TS），不伪装完整 JS 解析器。

const REASON_FINALLY = 'try/finally 无 catch'
const REASON_NO_TRY = '无 try/catch'

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

const N_NO_AWAIT_OUT_OF_SCOPE = `
function fireAndForget() {
  api.track('event').catch(() => {})
  api.track('other')
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
  ['无 await 的请求不在本守卫口径内（与旧守卫一致）', N_NO_AWAIT_OUT_OF_SCOPE],
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
  it.each(POSITIVE)('%s：报出的行都落在 await 语句上', (_name, source, _expected, filename = 'fixture.js') => {
    const lines = source.split('\n')
    for (const hit of scanUnguardedRequests(source, filename)) {
      expect(lines[hit.line - 1]).toContain('await')
    }
  })

  it('.vue 夹具报出原始文件行号', () => {
    const hits = scanUnguardedRequests(P_VUE_BLIND_SPOT, 'fixture.vue')
    expect(hits).toEqual([{ line: 8, reason: REASON_FINALLY, snippet: expect.any(String) }])
    expect(P_VUE_BLIND_SPOT.split('\n')[7]).toContain('await api.list()')
  })
})
