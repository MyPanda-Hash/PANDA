import { defineStore } from 'pinia'

export const useTabsStore = defineStore('tabs', {
  state: () => ({
    tabs: [{ title: '我的桌面', path: '/dashboard', affix: true }],
    active: '/dashboard',
  }),
  actions: {
    open(menu) {
      if (!this.tabs.find((t) => t.path === menu.path)) {
        this.tabs.push({ title: menu.title, path: menu.path, affix: false })
      }
      this.active = menu.path
    },
    setActive(path) {
      this.active = path
    },
    close(path) {
      const idx = this.tabs.findIndex((t) => t.path === path)
      if (idx === -1) return
      const tab = this.tabs[idx]
      if (tab.affix) return
      this.tabs.splice(idx, 1)
      if (this.active === path) {
        this.active = this.tabs[Math.min(idx, this.tabs.length - 1)]?.path || '/dashboard'
      }
    },
    closeOthers(path) {
      this.tabs = this.tabs.filter((t) => t.affix || t.path === path)
      this.active = path
    },
    closeAll() {
      this.tabs = this.tabs.filter((t) => t.affix)
      this.active = '/dashboard'
    },
  },
})
