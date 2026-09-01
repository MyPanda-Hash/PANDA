import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './styles/index.css'
import * as sqlPanelRuntime from './business/engine'
import { installPanelRuntime } from './core/panel-runtime'
import { i18n, registerDictFetcher } from './i18n'
import { useLocaleStore } from './stores/locale'

const app = createApp(App)

installPanelRuntime(sqlPanelRuntime)

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(createPinia())
app.use(router)
app.use(i18n)
// tt() 缺失词条自动批量补齐（所有语言通用）：注入 ensureDict，i18n 不直接依赖 business 层
registerDictFetcher((locale, keys) => useLocaleStore().ensureDict(locale, keys))
app.use(ElementPlus, { locale: zhCn }) // 组件文案随语言由 App.vue 的 el-config-provider 覆盖
app.mount('#app')
