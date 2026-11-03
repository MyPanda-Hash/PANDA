<template>
  <el-config-provider :locale="localeStore.epLocale">
    <router-view />
  </el-config-provider>
</template>

<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
import { useLocaleStore } from '@/stores/locale'

const localeStore = useLocaleStore()

// 启动即应用持久化的语言（html lang / title / i18n locale）
localeStore.apply(localeStore.current)
localeStore.loadAvailable()

// Alt+L：循环切换界面语言（注册表内）
function onAltL(e) {
  if (e.altKey && (e.key === 'l' || e.key === 'L' || e.code === 'KeyL')) {
    e.preventDefault()
    localeStore.cycle()
  }
}
onMounted(() => window.addEventListener('keydown', onAltL))
onBeforeUnmount(() => window.removeEventListener('keydown', onAltL))
</script>
