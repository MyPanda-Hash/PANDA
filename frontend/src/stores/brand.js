// 品牌配置：系统名由管理员改，默认「轻MES」。
// 没有落到后端：MES 侧没有通用的配置表，为一个展示名新建表+迁移不划算。
// 存在 localStorage，管理员在「关于」对话框里改，改完即时生效（刷新后仍在）。

import { defineStore } from 'pinia'

const STORAGE_KEY = 'mes.brand'
const DEFAULTS = {
  name: '轻MES',
  company: '缔智元',
}

function read() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULTS }
    const parsed = JSON.parse(raw)
    return {
      name: (parsed && parsed.name) || DEFAULTS.name,
      company: (parsed && parsed.company) || DEFAULTS.company,
    }
  } catch (_) {
    return { ...DEFAULTS }
  }
}

export const useBrandStore = defineStore('brand', {
  state: () => read(),
  actions: {
    /** 改系统名；传空则回落默认值。 */
    setBrand({ name, company }) {
      if (name !== undefined) this.name = String(name).trim() || DEFAULTS.name
      if (company !== undefined) this.company = String(company).trim() || DEFAULTS.company
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ name: this.name, company: this.company }))
      } catch (_) {}
      this.applyTitle()
    },
    /** 页面标题跟着系统名走（各路由自己也会设 title，这里只负责后缀）。 */
    applyTitle() {
      document.title = document.title.replace(/ · [^·]+$/, ` · ${this.name}`)
    },
    reset() {
      this.name = DEFAULTS.name
      this.company = DEFAULTS.company
      try {
        localStorage.removeItem(STORAGE_KEY)
      } catch (_) {}
    },
  },
})