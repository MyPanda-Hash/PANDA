import { defineStore } from 'pinia'

const DESK_DEFAULT = {
  quick: ['newOrder', 'quickReport', 'board'],
  showKpi: true,
  showProgress: true,
  showTodo: true,
}

function loadDeskSettings() {
  try {
    const s = JSON.parse(localStorage.getItem('mes_desk_settings') || 'null')
    return s ? { ...DESK_DEFAULT, ...s } : { ...DESK_DEFAULT }
  } catch (e) {
    return { ...DESK_DEFAULT }
  }
}

export const useAppStore = defineStore('app', {
  state: () => ({
    collapsed: localStorage.getItem('mes_collapsed') === '1',
    dark: localStorage.getItem('mes_dark') === '1',
    menuMode: localStorage.getItem('mes_menu_mode') || 'accordion',
    fullscreen: false,
    deskSettings: loadDeskSettings(),
    // T+ 门户形态：内容区最大化（隐藏左侧导航）
    maxContent: false,
    // 移动端：左侧导航抽屉开关（≤768px 生效）
    mobileNav: false,
    // 右侧帮助面板
    helpVisible: false,
    helpTab: 'dynamic',
    // MES 初始化向导（首次登录自动弹出，之后可从用户下拉再次打开）
    initWizardVisible: false,
    initDone: localStorage.getItem('mes_init_done') === '1',
  }),
  actions: {
    toggleCollapse() {
      this.collapsed = !this.collapsed
      localStorage.setItem('mes_collapsed', this.collapsed ? '1' : '0')
    },
    toggleDark() {
      this.dark = !this.dark
      localStorage.setItem('mes_dark', this.dark ? '1' : '0')
      document.documentElement.classList.toggle('dark', this.dark)
    },
    setMenuMode(mode) {
      this.menuMode = mode
      localStorage.setItem('mes_menu_mode', mode)
    },
    toggleFullscreen() {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen()
        this.fullscreen = true
      } else {
        document.exitFullscreen()
        this.fullscreen = false
      }
    },
    toggleMaxContent() {
      this.maxContent = !this.maxContent
    },
    toggleMobileNav() {
      this.mobileNav = !this.mobileNav
    },
    openHelp(tab) {
      this.helpVisible = true
      if (tab) this.helpTab = tab
    },
    closeHelp() {
      this.helpVisible = false
    },
    openInitWizard() {
      this.initWizardVisible = true
    },
    closeInitWizard(skip) {
      this.initWizardVisible = false
      if (skip) {
        this.initDone = true
        localStorage.setItem('mes_init_done', '1')
      }
    },
    finishInitWizard() {
      this.initDone = true
      localStorage.setItem('mes_init_done', '1')
    },
    saveDeskSettings(patch) {
      this.deskSettings = { ...this.deskSettings, ...patch }
      localStorage.setItem('mes_desk_settings', JSON.stringify(this.deskSettings))
    },
  },
})
