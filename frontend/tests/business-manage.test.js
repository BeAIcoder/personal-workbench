import { describe, expect, it } from 'vitest'
import manifest from '../src/modules/business-manage/manifest.js'
import { getModuleMenus, getModuleRoutes } from '../src/modules'
import {
  collectionTrendOption,
  kpiOption,
  leasingOption,
  litigationOption,
  occupancyOption,
  projectCompareOption,
} from '../src/modules/business-manage/chartOptions.js'

const NAMED_ROUTES = [
  { path: '/business/manage/control', name: 'business-manage-control', title: '内控管理' },
  { path: '/business/manage/weekly', name: 'business-manage-weekly', title: '云南项目周报' },
  { path: '/business/manage/weekly-qujing', name: 'business-manage-weekly-qujing', title: '曲靖项目周报' },
  { path: '/business/manage/monthly', name: 'business-manage-monthly', title: '月度工作情况' },
]

function seriesData(option, name) {
  const series = (option.series || []).find((item) => item.name === name)
  expect(series, `缺少 series：${name}`).toBeTruthy()
  return series.data
}

function numericValues(node, out = []) {
  if (typeof node === 'number' && Number.isFinite(node)) out.push(node)
  else if (Array.isArray(node)) for (const item of node) numericValues(item, out)
  else if (node && typeof node === 'object') for (const value of Object.values(node)) numericValues(value, out)
  return out
}

describe('经营管理模块注册', () => {
  it('manifest 声明四个命名路由与 /business/manage 重定向', () => {
    expect(manifest.name).toBe('business-manage')
    const parent = manifest.routes.find((route) => route.path === '/business/manage')
    expect(parent?.redirect).toBe('/business/manage/control')
    for (const expected of NAMED_ROUTES) {
      const route = manifest.routes.find((item) => item.path === expected.path)
      expect(route, `缺少路由 ${expected.path}`).toBeTruthy()
      expect(route.name).toBe(expected.name)
      expect(route.meta?.title).toBe(expected.title)
      expect(typeof route.component).toBe('function')
    }
  })

  it('模块注册中心已挂载四条路由（懒加载）', () => {
    const routes = getModuleRoutes()
    for (const expected of NAMED_ROUTES) {
      const route = routes.find((item) => item.path === expected.path)
      expect(route, `未注册路由 ${expected.path}`).toBeTruthy()
      expect(route.name).toBe(expected.name)
      expect(typeof route.component).toBe('function')
    }
  })

  it('侧栏菜单：经营分组 / 经营管理 / order 75 / DataBoard 图标', () => {
    const menu = getModuleMenus().find((item) => item.path === '/business/manage')
    expect(menu).toBeTruthy()
    expect(menu.group).toBe('经营')
    expect(menu.title).toBe('经营管理')
    expect(menu.icon).toBe('DataBoard')
    expect(menu.order).toBe(75)
  })
})

describe('经营管理图表：空数据不补 0', () => {
  const emptyOptions = [
    collectionTrendOption([]),
    occupancyOption([]),
    leasingOption([]),
    kpiOption([]),
    litigationOption([], []),
    projectCompareOption([]),
  ]

  it('六个图表在空数组下均显示「暂无数据」且 series.data 为空', () => {
    for (const option of emptyOptions) {
      expect(option.title?.text).toBe('暂无数据')
      expect(Array.isArray(option.series)).toBe(true)
      expect(option.series.length).toBeGreaterThan(0)
      for (const series of option.series) {
        expect(Array.isArray(series.data)).toBe(true)
        expect(series.data).toEqual([])
      }
      expect(numericValues(option.series.map((series) => series.data))).toEqual([])
    }
  })

  it('空态不保留 yAxisIndex，且笛卡尔图带空轴（否则 ECharts 会抛错）', () => {
    const cartesian = [collectionTrendOption([]), leasingOption([]), kpiOption([])]
    for (const option of cartesian) {
      expect(option.xAxis).toBeTruthy()
      expect(option.yAxis).toBeTruthy()
      for (const series of option.series) {
        expect(series.yAxisIndex).toBeUndefined()
      }
    }
    for (const option of emptyOptions) {
      for (const series of option.series) {
        expect(series.yAxisIndex).toBeUndefined()
      }
    }
  })

  it('非数组入参同样按空数据处理', () => {
    expect(collectionTrendOption(null).title?.text).toBe('暂无数据')
    expect(occupancyOption(undefined).series.every((series) => series.data.length === 0)).toBe(true)
    expect(kpiOption({}).title?.text).toBe('暂无数据')
  })
})

describe('经营管理图表：有数据时 series 值与输入一致', () => {
  it('收缴趋势：应收 / 已收 / 收缴率', () => {
    const option = collectionTrendOption([
      { period: '2026-09', project: '云南项目', receivable: 120, received: 90, rate: 0.75 },
    ])
    expect(seriesData(option, '应收')).toEqual([120])
    expect(seriesData(option, '已收')).toEqual([90])
    expect(seriesData(option, '收缴率')).toEqual([75])
    expect(option.xAxis.data).toEqual(['2026-09'])
  })

  it('出租率：按项目分组，0–1 与百分数口径归一', () => {
    const option = occupancyOption([
      { period: '2026-09', project: '云南项目', rate: 0.8846 },
      { period: '2026-09', project: '曲靖项目', rate: 88.2 },
    ])
    expect(seriesData(option, '云南项目')).toEqual([88.46])
    expect(seriesData(option, '曲靖项目')).toEqual([88.2])
  })

  it('招商进度：计划 / 实际 / 完成率', () => {
    const option = leasingOption([
      { period: '2026-09', project: '云南项目', plan: 1000, done: 600, rate: 0.6 },
    ])
    expect(seriesData(option, '计划')).toEqual([1000])
    expect(seriesData(option, '实际')).toEqual([600])
    expect(seriesData(option, '完成率')).toEqual([60])
  })

  it('责任书完成率：横向条形图与指标名一致', () => {
    const option = kpiOption([
      { name: '营业总收入', target: 200, actual: 150, rate: 0.75 },
      { name: '累计收缴率', target: 100, actual: 65, rate: 0.65 },
      { name: '利润总额', target: 200, actual: 298, rate: 1.49 },
    ])
    expect(option.yAxis.data).toEqual(['营业总收入', '累计收缴率', '利润总额'])
    // 比率按小数存：1.49 是 149%，不能显示成 1.49%
    expect(seriesData(option, '完成率')).toEqual([75, 65, 149])
  })

  it('诉讼阶段饼图与金额 Top10', () => {
    const option = litigationOption(
      [{ stage: '一审', count: 2, amount: 100 }],
      [{ name: '品牌A', amount: 500, balance: 200 }],
    )
    expect(seriesData(option, '诉讼阶段')).toEqual([{ name: '一审', value: 2 }])
    expect(seriesData(option, '诉讼金额')).toEqual([500])
    expect(seriesData(option, '还欠金额')).toEqual([200])
    expect(option.yAxis[1].data).toEqual(['品牌A'])
  })

  it('项目对比：收缴率 / 出租率 / 新签面积', () => {
    const option = projectCompareOption([
      { project: '云南项目', collection_rate: 0.9, occupancy_rate: 0.88, new_area: 1200 },
    ])
    expect(seriesData(option, '收缴率')).toEqual([90])
    expect(seriesData(option, '出租率')).toEqual([88])
    expect(seriesData(option, '新签面积')).toEqual([1200])
    expect(option.xAxis.data).toEqual(['云南项目'])
  })
})
