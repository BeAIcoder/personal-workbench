const CHUNK_RELOAD_KEY = 'personal-workbench:vite-preload-reload'

export function installChunkRecovery(win, router) {
  win.addEventListener('vite:preloadError', (event) => {
    let storage
    let alreadyReloaded
    try {
      storage = win.sessionStorage
      alreadyReloaded = storage.getItem(CHUNK_RELOAD_KEY) === win.location.href
    } catch {
      return
    }
    if (alreadyReloaded) return

    try {
      storage.setItem(CHUNK_RELOAD_KEY, win.location.href)
    } catch {
      return
    }
    event.preventDefault()
    win.location.reload()
  })

  router.afterEach((_to, _from, failure) => {
    if (failure) return
    try {
      win.sessionStorage.removeItem(CHUNK_RELOAD_KEY)
    } catch {
      return
    }
  })
}
