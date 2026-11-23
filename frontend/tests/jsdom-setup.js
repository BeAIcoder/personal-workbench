// jsdom 环境补丁：只被显式声明 `@vitest-environment jsdom` 的测试文件引入，
// 避免给现有 1,300+ 个 node 环境测试增加任何全局副作用。
// Element Plus 的表格/弹窗/滚动容器依赖浏览器测量 API，jsdom 没有实现。

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (!globalThis.ResizeObserver) globalThis.ResizeObserver = ResizeObserverStub

if (!globalThis.IntersectionObserver) {
  globalThis.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return [] }
  }
}

if (!globalThis.matchMedia) {
  globalThis.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return false },
  })
}

if (typeof window !== 'undefined' && !window.scrollTo) window.scrollTo = () => {}

// 弹窗关闭后清理 teleport 到 body 的残留节点，避免用例间互相污染
export function cleanupBodyTeleports() {
  if (typeof document === 'undefined') return
  for (const node of document.body.querySelectorAll('.el-popper, .el-overlay')) node.remove()
}
