import { defineStore } from 'pinia'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import zhTw from 'element-plus/es/locale/lang/zh-tw'
import enLocale from 'element-plus/es/locale/lang/en'
import jaLocale from 'element-plus/es/locale/lang/ja'
import koLocale from 'element-plus/es/locale/lang/ko'
import deLocale from 'element-plus/es/locale/lang/de'
import frLocale from 'element-plus/es/locale/lang/fr'
import esLocale from 'element-plus/es/locale/lang/es'
import ruLocale from 'element-plus/es/locale/lang/ru'
import thLocale from 'element-plus/es/locale/lang/th'
import { i18n, LOCALE_KEY, tt } from '@/i18n'
import { localeList, localeDict } from '@/business/engine'

/** Element Plus 组件文案包映射（语言键 → EP locale） */
export const EP_LOCALES = {
  'zh-CN': zhCn,
  'zh-TW': zhTw,
  en: enLocale,
  ja: jaLocale,
  ko: koLocale,
  de: deLocale,
  fr: frLocale,
  es: esLocale,
  ru: ruLocale,
  th: thLocale,
}

const DICT_BATCH_LIMIT = 50

export const useLocaleStore = defineStore('locale', {
  state: () => ({
    current: localStorage.getItem(LOCALE_KEY) || 'zh-CN',
    available: [{ locale: 'zh-CN', nameZh: '简体中文', nameNative: '简体中文' }],
    loaded: false,
  }),
  getters: {
    epLocale: (state) => EP_LOCALES[state.current] || zhCn,
    isZh: (state) => !state.current || state.current === 'zh-CN',
    currentShort: (state) => {
      const name = (state.available.find((l) => l.locale === state.current) || {}).nameNative
        || (state.current === 'zh-CN' ? '简体中文' : state.current)
      return name.length > 6 ? name.slice(0, 6) : name
    },
  },
  actions: {
    /** 应用语言到 i18n 实例 + html lang + 标题（纯前端切换，无需后端参与） */
    apply(locale) {
      this.current = locale
      i18n.global.locale.value = locale
      localStorage.setItem(LOCALE_KEY, locale)
      document.documentElement.setAttribute('lang', locale)
      document.title = tt(document.title.includes(' · ') ? document.title.split(' · ')[0] : document.title) + ' · 轻MES'
    },
    /** 拉取可选语言（sys_locale 注册表；失败回退仅中文，loaded 不置位以便下次重试） */
    async loadAvailable() {
      try {
        const list = await localeList()
        if (Array.isArray(list) && list.length) {
          this.available = list
          this.loaded = true
        }
      } catch (e) {
        /* 后端不可达：仅中文 */
      }
    },
    /**
     * 补齐词条：只把「当前 locale 缺失的键」发给 /locale/dict（后端 30s 缓存 +
     * sys_translation 落库 + 机翻），merge 進来。静态包/已有机翻结果不会被覆盖。
     */
    async ensureDict(locale, keys) {
      const target = locale || this.current
      if (!target || target === 'zh-CN') return
      const dict = i18n.global.getLocaleMessage(target)?.biz || {}
      const missing = [...new Set(keys || [])].filter((k) => (
        typeof k === 'string' && k.trim() && k.length <= 200 && !(k in dict)
      ))
      if (!missing.length) return
      for (let i = 0; i < missing.length; i += DICT_BATCH_LIMIT) {
        const batch = missing.slice(i, i + DICT_BATCH_LIMIT)
        try {
          const got = await localeDict({ locale: target, keys: batch })
          if (got && Object.keys(got).length) {
            i18n.global.mergeLocaleMessage(target, { biz: got })
          }
        } catch (e) {
          /* 翻译服务不可用：tt() 回退中文 */
        }
      }
    },
    /** 切换语言并补齐给定页面的词条（keys 为当前页可见标签集，可空） */
    async set(locale, keys = []) {
      if (locale === this.current) return
      this.apply(locale)
      if (keys.length) await this.ensureDict(locale, keys)
    },
    /** Alt+L 循环切换（可用语言内） */
    async cycle() {
      if (!this.loaded) await this.loadAvailable()
      const list = this.available.length ? this.available : [{ locale: 'zh-CN' }]
      const idx = list.findIndex((l) => l.locale === this.current)
      const next = list[(idx + 1 + list.length) % list.length]
      await this.set(next.locale)
    },
  },
})
