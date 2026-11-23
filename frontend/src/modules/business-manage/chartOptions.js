// 经营管理图表配置：纯函数，不触碰 DOM，便于单元测试。
// 空数据约定：series.data 一律为空数组（不补 0），标题区显示「暂无数据」。
const EMPTY_TEXT = '暂无数据'
const PALETTE = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399', '#9254de']

function num(value) {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function round(value, digits = 2) {
  return Number(Number(value).toFixed(digits))
}

// 比率兼容 0–1 小数与 0–100 百分数两种导入口径
function percentPoint(value) {
  const parsed = num(value)
  if (parsed === null) return null
  // 本模块比率统一按小数存（0–5，允许完成率 >100%），超过 5 才视为已是百分点
  const point = Math.abs(parsed) <= 5 ? parsed * 100 : parsed
  return round(point)
}

function rateOf(row) {
  const rate = percentPoint(row?.rate)
  if (rate !== null) return rate
  const target = num(row?.target)
  const actual = num(row?.actual)
  if (target !== null && target !== 0 && actual !== null) return round((actual / target) * 100)
  return null
}

function unique(values) {
  return [...new Set(values.filter((value) => value !== null && value !== undefined && value !== ''))]
}

function emptyOption(series = []) {
  // 空态不能保留 yAxisIndex：空配置只有一条 Y 轴，ECharts 会在 cartesian2d 抛
  // 「Cannot read properties of undefined (reading 'get')」。同时给笛卡尔系列补上空轴。
  const cartesian = series.some((item) => item.type === 'bar' || item.type === 'line')
  const option = {
    title: {
      text: EMPTY_TEXT,
      left: 'center',
      top: 'middle',
      textStyle: { color: '#909399', fontSize: 13, fontWeight: 'normal' },
    },
    tooltip: {},
    series: series.map(({ yAxisIndex, ...item }) => ({ ...item, data: [] })),
  }
  if (cartesian) {
    option.xAxis = { type: 'category', data: [] }
    option.yAxis = { type: 'value' }
  }
  return option
}

function allEmpty(series) {
  return series.every((item) => (item.data || []).every((value) => value === null))
}

function axisLabelPercent() {
  return { formatter: '{value}%' }
}

export function collectionTrendOption(rows) {
  const definitions = [
    { name: '应收', type: 'bar' },
    { name: '已收', type: 'bar' },
    { name: '收缴率', type: 'line', yAxisIndex: 1 },
  ]
  const list = Array.isArray(rows) ? rows : []
  if (!list.length) return emptyOption(definitions)

  const periods = unique(list.map((row) => row?.period))
  if (!periods.length) return emptyOption(definitions)

  const buckets = new Map(periods.map((period) => [period, { receivable: null, received: null, rates: [] }]))
  for (const row of list) {
    const bucket = row ? buckets.get(row.period) : null
    if (!bucket) continue
    const receivable = num(row.receivable)
    const received = num(row.received)
    if (receivable !== null) bucket.receivable = (bucket.receivable ?? 0) + receivable
    if (received !== null) bucket.received = (bucket.received ?? 0) + received
    const rate = num(row.rate)
    if (rate !== null) bucket.rates.push(rate)
  }

  const receivableData = periods.map((period) => {
    const value = buckets.get(period).receivable
    return value === null ? null : round(value)
  })
  const receivedData = periods.map((period) => {
    const value = buckets.get(period).received
    return value === null ? null : round(value)
  })
  const rateData = periods.map((period) => {
    const bucket = buckets.get(period)
    if (bucket.receivable !== null && bucket.receivable !== 0 && bucket.received !== null) {
      return round((bucket.received / bucket.receivable) * 100)
    }
    if (bucket.rates.length) return percentPoint(bucket.rates.reduce((sum, value) => sum + value, 0) / bucket.rates.length)
    return null
  })

  const series = [
    { name: '应收', type: 'bar', barMaxWidth: 26, itemStyle: { color: PALETTE[0] }, data: receivableData },
    { name: '已收', type: 'bar', barMaxWidth: 26, itemStyle: { color: PALETTE[1] }, data: receivedData },
    { name: '收缴率', type: 'line', yAxisIndex: 1, itemStyle: { color: PALETTE[2] }, data: rateData },
  ]
  if (allEmpty(series)) return emptyOption(definitions)

  return {
    tooltip: { trigger: 'axis' },
    legend: { data: definitions.map((item) => item.name), top: 0 },
    grid: { left: 12, right: 16, top: 40, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: periods },
    yAxis: [
      { type: 'value', name: '金额' },
      { type: 'value', name: '收缴率', axisLabel: axisLabelPercent(), max: 100 },
    ],
    series,
  }
}

export function occupancyOption(rows) {
  const list = Array.isArray(rows) ? rows : []
  const definitions = [{ name: '出租率', type: 'bar' }]
  if (!list.length) return emptyOption(definitions)

  const periods = unique(list.map((row) => row?.period))
  if (!periods.length) return emptyOption(definitions)
  const projects = unique(list.map((row) => row?.project || '合计'))

  const series = projects.map((project, index) => ({
    name: project,
    type: 'bar',
    barMaxWidth: 32,
    itemStyle: { color: PALETTE[index % PALETTE.length] },
    data: periods.map((period) => {
      const row = list.find((item) => item?.period === period && (item.project || '合计') === project)
      return row ? percentPoint(row.rate) : null
    }),
  }))
  if (allEmpty(series)) return emptyOption(definitions)

  return {
    tooltip: { trigger: 'axis', valueFormatter: (value) => (value === null || value === undefined ? '—' : `${value}%`) },
    legend: { top: 0 },
    grid: { left: 12, right: 16, top: 40, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: periods },
    yAxis: { type: 'value', name: '出租率', axisLabel: axisLabelPercent(), max: 100 },
    series,
  }
}

export function leasingOption(rows) {
  const list = Array.isArray(rows) ? rows : []
  const definitions = [
    { name: '计划', type: 'bar' },
    { name: '实际', type: 'bar' },
    { name: '完成率', type: 'line', yAxisIndex: 1 },
  ]
  if (!list.length) return emptyOption(definitions)

  const periods = unique(list.map((row) => row?.period))
  if (!periods.length) return emptyOption(definitions)

  const planData = periods.map((period) => {
    const value = list.filter((row) => row?.period === period).reduce((sum, row) => sum + (num(row.plan) ?? 0), 0)
    return list.some((row) => row?.period === period && num(row.plan) !== null) ? round(value) : null
  })
  const doneData = periods.map((period) => {
    const value = list.filter((row) => row?.period === period).reduce((sum, row) => sum + (num(row.done) ?? 0), 0)
    return list.some((row) => row?.period === period && num(row.done) !== null) ? round(value) : null
  })
  const rateData = periods.map((period) => {
    const periodRows = list.filter((row) => row?.period === period)
    const plan = periodRows.reduce((sum, row) => sum + (num(row.plan) ?? 0), 0)
    const done = periodRows.reduce((sum, row) => sum + (num(row.done) ?? 0), 0)
    if (plan) return round((done / plan) * 100)
    const rates = periodRows.map((row) => num(row.rate)).filter((value) => value !== null)
    return rates.length ? percentPoint(rates.reduce((sum, value) => sum + value, 0) / rates.length) : null
  })

  const series = [
    { name: '计划', type: 'bar', barMaxWidth: 26, itemStyle: { color: PALETTE[0] }, data: planData },
    { name: '实际', type: 'bar', barMaxWidth: 26, itemStyle: { color: PALETTE[1] }, data: doneData },
    { name: '完成率', type: 'line', yAxisIndex: 1, itemStyle: { color: PALETTE[2] }, data: rateData },
  ]
  if (allEmpty(series)) return emptyOption(definitions)

  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 12, right: 16, top: 40, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: periods },
    yAxis: [
      { type: 'value', name: '面积 / 数量' },
      { type: 'value', name: '完成率', axisLabel: axisLabelPercent(), max: 100 },
    ],
    series,
  }
}

export function kpiOption(rows) {
  const list = Array.isArray(rows) ? rows : []
  if (!list.length) return emptyOption([{ name: '完成率', type: 'bar' }])

  const names = list.map((row, index) => row?.name || `指标${index + 1}`)
  const data = list.map((row) => rateOf(row))
  if (data.every((value) => value === null)) return emptyOption([{ name: '完成率', type: 'bar' }])

  return {
    tooltip: { trigger: 'axis', valueFormatter: (value) => (value === null || value === undefined ? '—' : `${value}%`) },
    grid: { left: 12, right: 48, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: axisLabelPercent() },
    yAxis: { type: 'category', data: names, inverse: true },
    series: [
      {
        name: '完成率',
        type: 'bar',
        barMaxWidth: 20,
        itemStyle: { color: PALETTE[0] },
        label: {
          show: true,
          position: 'right',
          formatter: ({ value }) => (value === null || value === undefined ? '' : `${value}%`),
        },
        data,
      },
    ],
  }
}

export function litigationOption(byStage, top) {
  const stages = Array.isArray(byStage) ? byStage : []
  const topRows = (Array.isArray(top) ? top : []).slice(0, 10)
  const definitions = [
    { name: '诉讼阶段', type: 'pie' },
    { name: '诉讼金额', type: 'bar' },
  ]

  const stageData = stages
    .map((row) => {
      const value = num(row?.count) ?? num(row?.amount)
      return value === null ? null : { name: row?.stage || '未标注', value }
    })
    .filter(Boolean)
  const topNames = topRows.map((row, index) => row?.name || `案件${index + 1}`)
  const amountData = topRows.map((row) => num(row?.amount))
  const balanceData = topRows.map((row) => num(row?.balance))

  if (!stageData.length && amountData.every((value) => value === null) && balanceData.every((value) => value === null)) {
    return emptyOption(definitions)
  }

  return {
    tooltip: { trigger: 'item' },
    legend: { top: 0 },
    grid: [
      { left: '4%', right: '55%', top: 44, bottom: 12, containLabel: true },
      { left: '52%', right: '4%', top: 44, bottom: 12, containLabel: true },
    ],
    xAxis: [
      { type: 'value', show: false },
      { type: 'value', gridIndex: 1, name: '金额' },
    ],
    yAxis: [
      { type: 'category', show: false },
      { type: 'category', gridIndex: 1, data: topNames, inverse: true },
    ],
    series: [
      {
        name: '诉讼阶段',
        type: 'pie',
        radius: ['36%', '62%'],
        center: ['27%', '56%'],
        label: { formatter: '{b}\n{c}' },
        data: stageData,
      },
      {
        name: '诉讼金额',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 18,
        itemStyle: { color: PALETTE[0] },
        data: amountData,
      },
      {
        name: '还欠金额',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 18,
        itemStyle: { color: PALETTE[3] },
        data: balanceData,
      },
    ],
  }
}

export function projectCompareOption(rows) {
  const list = Array.isArray(rows) ? rows : []
  const definitions = [
    { name: '收缴率', type: 'bar' },
    { name: '出租率', type: 'bar' },
    { name: '新签面积', type: 'bar', yAxisIndex: 1 },
  ]
  if (!list.length) return emptyOption(definitions)

  const projects = list.map((row, index) => row?.project || `项目${index + 1}`)
  const series = [
    {
      name: '收缴率',
      type: 'bar',
      barMaxWidth: 26,
      itemStyle: { color: PALETTE[1] },
      data: list.map((row) => percentPoint(row?.collection_rate)),
    },
    {
      name: '出租率',
      type: 'bar',
      barMaxWidth: 26,
      itemStyle: { color: PALETTE[0] },
      data: list.map((row) => percentPoint(row?.occupancy_rate)),
    },
    {
      name: '新签面积',
      type: 'bar',
      yAxisIndex: 1,
      barMaxWidth: 26,
      itemStyle: { color: PALETTE[2] },
      data: list.map((row) => num(row?.new_area)),
    },
  ]
  if (allEmpty(series)) return emptyOption(definitions)

  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 12, right: 16, top: 40, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: projects },
    yAxis: [
      { type: 'value', name: '比率', axisLabel: axisLabelPercent(), max: 100 },
      { type: 'value', name: '新签面积' },
    ],
    series,
  }
}

// 空铺构成 / 预警原因构成（饼图）
export function compositionOption(rows) {
  const list = (Array.isArray(rows) ? rows : [])
    .map((row) => ({ name: row?.name || row?.reason || '未标注', value: num(row?.value ?? row?.count) }))
    .filter((row) => row.value !== null)
  if (!list.length) return emptyOption([{ name: '构成', type: 'pie' }])

  return {
    tooltip: { trigger: 'item', formatter: '{b}<br/>{c}（{d}%）' },
    legend: { bottom: 0 },
    series: [
      {
        name: '构成',
        type: 'pie',
        radius: ['38%', '64%'],
        center: ['50%', '46%'],
        label: { formatter: '{b}\n{d}%' },
        data: list,
      },
    ],
  }
}

// 收缴漏斗：取最新期间（多项目求和）
export function collectionFunnelOption(rows) {
  const list = Array.isArray(rows) ? rows : []
  const definitions = [{ name: '收缴漏斗', type: 'funnel' }]
  if (!list.length) return emptyOption(definitions)

  const periods = unique(list.map((row) => row?.period)).sort()
  const latest = periods.length ? periods[periods.length - 1] : null
  const scoped = latest ? list.filter((row) => row?.period === latest) : list
  let receivable = null
  let received = null
  for (const row of scoped) {
    const currentReceivable = num(row?.receivable)
    const currentReceived = num(row?.received)
    if (currentReceivable !== null) receivable = (receivable ?? 0) + currentReceivable
    if (currentReceived !== null) received = (received ?? 0) + currentReceived
  }

  const data = []
  if (receivable !== null) data.push({ name: '应收', value: round(receivable) })
  if (received !== null) data.push({ name: '已收', value: round(received) })
  if (receivable !== null && received !== null && receivable >= received) {
    data.push({ name: '未收', value: round(receivable - received) })
  }
  if (!data.length) return emptyOption(definitions)

  return {
    tooltip: { trigger: 'item', formatter: '{b}<br/>{c}' },
    series: [
      {
        name: '收缴漏斗',
        type: 'funnel',
        left: '10%',
        right: '10%',
        top: 24,
        bottom: 16,
        sort: 'descending',
        label: { formatter: '{b}：{c}' },
        data,
      },
    ],
  }
}

// 新签 / 续签对比（月度）
export function signingOption(rows) {
  const list = Array.isArray(rows) ? rows : []
  const definitions = [{ name: '新签', type: 'bar' }, { name: '续签', type: 'bar' }]
  if (!list.length) return emptyOption(definitions)

  const names = list.map((row, index) => row?.name || `指标${index + 1}`)
  const data = list.map((row) => num(row?.value))
  if (data.every((value) => value === null)) return emptyOption(definitions)

  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 12, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: names },
    yAxis: { type: 'value', name: list[0]?.unit || '' },
    series: [
      {
        name: '新签',
        type: 'bar',
        barMaxWidth: 40,
        itemStyle: { color: PALETTE[1] },
        label: { show: true, position: 'top' },
        data,
      },
    ],
  }
}
