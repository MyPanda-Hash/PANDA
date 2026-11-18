import { defineStore } from 'pinia'
import { apiLogin, apiGetUserInfo } from '@/business/api'

export const useUserStore = defineStore('user', {
  state: () => {
    let ui = null
    try { ui = JSON.parse(localStorage.getItem('mes_user') || 'null') } catch {}
    return {
      token: (() => { const t = localStorage.getItem('mes_token'); return t && t !== 'undefined' ? t : '' })(),
      userInfo: ui,
      factory: (() => { try { return JSON.parse(localStorage.getItem('mes_factory') || 'null') } catch { return null } })(),
      factories: [],
      loginDate: '',
      roleCode: ui?.roleCode || '',
      isAdmin: !!ui?.isAdmin,
      visiblePanels: Array.isArray(ui?.visiblePanels) ? ui.visiblePanels : [],
      approvePanels: Array.isArray(ui?.approvePanels) ? ui.approvePanels : [],
      panelPerms: (ui && typeof ui.panelPerms === 'object' && ui.panelPerms) || {},
    }
  },
  getters: {
    isLogin: (s) => !!s.token,
    realName: (s) => s.userInfo?.realName || s.userInfo?.userName || '',
    factoryName: (s) => s.factory?.name || '',
    // T+ 顶栏中区：登录日期（登录时记录）
    loginDateText: (s) => s.loginDate || localStorage.getItem('mes_login_date') || '--',
    // T+ 顶栏中区：服务到期时间（后续由 SQL 后端账号信息提供）
    serviceEnd: (s) => s.userInfo?.serviceEnd || '2027-08-13',
    account: (s) => s.userInfo?.userName || '',
  },
  actions: {
    async login(payload) {
      const res = await apiLogin(payload)
      this.token = res.token
      this.userInfo = res.user
      this.applyPerms(res.user)
      const today = new Date().toISOString().slice(0, 10)
      this.loginDate = today
      localStorage.setItem('mes_token', res.token)
      localStorage.setItem('mes_user', JSON.stringify(res.user))
      localStorage.setItem('mes_login_date', today)
      return res
    },
    // 从登录/用户信息中提取角色权限
    applyPerms(u) {
      this.roleCode = u?.roleCode || ''
      this.isAdmin = !!u?.isAdmin
      this.visiblePanels = Array.isArray(u?.visiblePanels) ? u.visiblePanels : []
      this.approvePanels = Array.isArray(u?.approvePanels) ? u.approvePanels : []
      this.panelPerms = (u && typeof u.panelPerms === 'object' && u.panelPerms) || {}
      this.syncDeskRole()
    },
    /**
     * 把 MES 的账号等级映射成 AI 工作台的默认视角，写进同源 localStorage。
     *
     * 为什么由 MES 写：合并账号体系后 AI 不再有自己的登录与角色来源，
     * 「进来先看简报还是先看待办」必须按 MES 的岗位决定，而不是让用户自己选。
     * desk 侧读 dnx-role（见 frontend-desk/js/main.js 的 currentRole）。
     */
    syncDeskRole() {
      const role = this.isAdmin
        ? 'manager'
        : ['FOREMAN', 'WORKER', 'PLANNER'].includes(this.roleCode)
          ? 'supervisor'
          : 'staff'
      try {
        localStorage.setItem('dnx-role', role)
      } catch (_) {
        /* 隐私模式下写不了就按 desk 默认走 */
      }
    },
    // 刷新权限（角色/面板配置变更后调用）
    async fetchPerms() {
      const { apiGetPerms } = await import('@/business/api')
      const p = await apiGetPerms()
      if (!p) return
      this.roleCode = p.roleCode || ''
      this.isAdmin = !!p.isAdmin
      this.visiblePanels = Array.isArray(p.visiblePanels) ? p.visiblePanels : []
      this.approvePanels = Array.isArray(p.approvePanels) ? p.approvePanels : []
      this.panelPerms = (p && typeof p.panelPerms === 'object' && p.panelPerms) || {}
      if (this.userInfo) {
        this.userInfo = { ...this.userInfo, roleCode: this.roleCode, isAdmin: this.isAdmin, visiblePanels: this.visiblePanels, approvePanels: this.approvePanels, panelPerms: this.panelPerms }
        localStorage.setItem('mes_user', JSON.stringify(this.userInfo))
      }
    },
    // 11 项权限判定：admin 恒真；旧会话（无矩阵数据）不拦截非 view 动作，等重新登录后生效
    hasPerm(panel, action) {
      if (this.isAdmin) return true
      const perms = this.panelPerms?.[panel]
      if (Array.isArray(perms)) return perms.includes(action)
      if (action === 'view') return this.visiblePanels.includes(panel)
      return Object.keys(this.panelPerms || {}).length === 0 && this.visiblePanels.length > 0
    },
    async fetchUserInfo() {
      const info = await apiGetUserInfo()
      this.userInfo = info
      this.applyPerms(info)
      localStorage.setItem('mes_user', JSON.stringify(info))
    },
    async fetchFactories() {
      const { apiGetFactories } = await import('@/business/api')
      this.factories = await apiGetFactories()
      const currentCode = this.factory?.code || this.userInfo?.factoryCode
      const latest = this.factories.find((item) => item.code === currentCode) || this.factories[0]
      if (latest) {
        // Replace the whole cached object so corrected names/addresses take effect
        // after a database repair without requiring users to clear localStorage.
        this.factory = latest
        localStorage.setItem('mes_factory', JSON.stringify(this.factory))
      }
    },
    /**
     * 切换账套。
     *
     * 必须整页重载：面板数据、页签、看板、图表都是上一个账套的，
     * 只换内存里的 factory 而不重新取数，界面会继续显示旧账套的内容
     * ——这正是改之前「切换没反应」的根源。重载后请求头带新账套，
     * 后端按新账套返回数据。
     */
    switchFactory(r) {
      if (!r || (this.factory && this.factory.code === r.code)) return
      this.factory = r
      localStorage.setItem('mes_factory', JSON.stringify(r))
      // 用 replace 避免在浏览器历史里堆一串「切换账套」记录
      location.replace('/#/dashboard')
      location.reload()
    },
    logout() {
      this.token = ''
      this.userInfo = null
      this.roleCode = ''
      this.isAdmin = false
      this.visiblePanels = []
      this.approvePanels = []
      this.panelPerms = {}
      localStorage.removeItem('mes_token')
      localStorage.removeItem('mes_user')
    },

  },
})
