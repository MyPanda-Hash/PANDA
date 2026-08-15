import { defineStore } from 'pinia'
import { apiLogin, apiGetUserInfo, USE_MOCK } from '@/business/api'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('mes_token') || '',
    userInfo: JSON.parse(localStorage.getItem('mes_user') || 'null'),
    factory: JSON.parse(localStorage.getItem('mes_factory') || 'null'),
    factories: [],
    loginDate: '',
  }),
  getters: {
    isLogin: (s) => !!s.token,
    realName: (s) => s.userInfo?.realName || s.userInfo?.userName || '',
    factoryName: (s) => s.factory?.name || '',
    // T+ 顶栏中区：登录日期（登录时记录）
    loginDateText: (s) => s.loginDate || localStorage.getItem('mes_login_date') || '--',
    // T+ 顶栏中区：服务到期时间（mock 默认一年有效期）
    serviceEnd: (s) => s.userInfo?.serviceEnd || '2027-08-13',
    account: (s) => s.userInfo?.userName || '',
  },
  actions: {
    async login(payload) {
      const res = await apiLogin(payload)
      this.token = res.token
      this.userInfo = res.user
      const today = new Date().toISOString().slice(0, 10)
      this.loginDate = today
      localStorage.setItem('mes_token', res.token)
      localStorage.setItem('mes_user', JSON.stringify(res.user))
      localStorage.setItem('mes_login_date', today)
      return res
    },
    async fetchUserInfo() {
      const info = await apiGetUserInfo()
      this.userInfo = info
      localStorage.setItem('mes_user', JSON.stringify(info))
    },
    async fetchFactories() {
      const { apiGetFactories } = await import('@/business/api')
      this.factories = await apiGetFactories()
      if (!this.factory && this.factories.length) {
        this.factory = this.factories[0]
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
      localStorage.removeItem('mes_token')
      localStorage.removeItem('mes_user')
    },
    isMock() {
      return USE_MOCK
    },
  },
})
