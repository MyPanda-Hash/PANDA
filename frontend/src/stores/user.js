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
      if (this.userInfo) {
        this.userInfo = { ...this.userInfo, roleCode: this.roleCode, isAdmin: this.isAdmin, visiblePanels: this.visiblePanels, approvePanels: this.approvePanels }
        localStorage.setItem('mes_user', JSON.stringify(this.userInfo))
      }
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
    switchFactory(r) {
      this.factory = r
      localStorage.setItem('mes_factory', JSON.stringify(r))
    },
    logout() {
      this.token = ''
      this.userInfo = null
      this.roleCode = ''
      this.isAdmin = false
      this.visiblePanels = []
      this.approvePanels = []
      localStorage.removeItem('mes_token')
      localStorage.removeItem('mes_user')
    },

  },
})
