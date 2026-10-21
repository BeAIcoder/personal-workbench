// 原生 <input type="file"> 取值辅助：取值后立即清空 input.value，
// 否则复选同一文件时浏览器不会再次触发 change
export function takeFile(e) {
  const file = e.target.files?.[0] || null
  e.target.value = ''
  return file
}

export function takeFiles(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  return files
}
