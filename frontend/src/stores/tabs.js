import { defineStore } from 'pinia'

export const useTabsStore = defineStore('tabs', {
  state: () => ({
    tabs: [{ title: '我的桌面', path: '/dashboard', affix: true }],
    active: '/dashboard',
  }),
  actions: {
    open(menu) {
      const ex = this.tabs.find((t) => t.path === menu.path)
      if (ex) {
        // 同一路径再次打开：更新标题与查询参数（如单据表单的 ?code=）
        // 防污染：已打开具体单据标签时，忽略「X-新增」标签请求（避免标题/查询被覆盖）
        const isNewReq = typeof menu.title === 'string' && menu.title.endsWith('-新增')
        const exIsNew = typeof ex.title === 'string' && ex.title.endsWith('-新增')
        if (isNewReq && !exIsNew) {
          this.active = menu.path
          return
        }
        if (menu.title) ex.title = menu.title
        if (menu.query) ex.query = { ...menu.query }
      } else {
        this.tabs.push({ title: menu.title, path: menu.path, query: menu.query ? { ...menu.query } : {}, affix: false })
      }
      this.active = menu.path
    },
    // 路由变化时同步活动页签的 query/标题——保证切换页签回来能恢复（含 ?code= 单据参数，修复「生单后切换单据数据丢失」）
    sync(route) {
      // 只同步查询参数（如 ?code=单据号），不覆盖标题——标题由打开方设置（如 生产加工单-MO-xxx）
      const t = this.tabs.find((x) => x.path === route.path)
      if (t) t.query = { ...route.query }
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
