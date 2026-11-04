import dayjs from 'dayjs'

// 跨日刷新间隔：一分钟足够让「今天/问候」在午夜后自然更新，成本可忽略。
export const DAY_TICK_MS = 60 * 1000

export function todayTextOf(now = new Date()) {
  return dayjs(now).format('YYYY年MM月DD日 dddd')
}

export function greetingOf(now = new Date()) {
  const h = dayjs(now).hour()
  if (h < 6) return '夜深了，注意休息'
  if (h < 9) return '早上好'
  if (h < 12) return '上午好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
}

// 可启动/停止的时钟：组件在 onMounted 启动、onBeforeUnmount 停止。
export function createDayClock(now = () => new Date()) {
  let timer = null
  return {
    start(onTick) {
      if (timer !== null) clearInterval(timer)
      onTick(now())
      timer = setInterval(() => onTick(now()), DAY_TICK_MS)
    },
    stop() {
      if (timer !== null) {
        clearInterval(timer)
        timer = null
      }
    },
  }
}
