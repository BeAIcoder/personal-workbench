// 可点击非按钮元素的键盘激活：Enter / Space 与原生按钮行为一致。
export function isActivationKey(event) {
  if (!event) return false
  return event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar'
}

export function activationKeydown(handler) {
  return (event) => {
    if (!isActivationKey(event)) return
    event.preventDefault?.()
    handler(event)
  }
}
