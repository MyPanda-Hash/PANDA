<template>
  <div class="portal" :class="{ dark: app.dark }">
    <TopBar />
    <div class="portal-body">
      <LeftNav v-if="!app.maxContent" />
      <div class="portal-main">
        <TabsBar />
        <div class="portal-content">
          <router-view v-slot="{ Component }">
            <keep-alive :max="20">
              <component :is="Component" />
            </keep-alive>
          </router-view>
        </div>
      </div>
    </div>

    <!-- 移动端：抽屉遮罩 -->
    <transition name="fade">
      <div v-if="app.mobileNav" class="nav-mask" @click="app.toggleMobileNav()"></div>
    </transition>

    <!-- T+ 浮层：右侧帮助面板 / 初始化向导 -->
    <HelpPanel />
    <InitWizard />
  </div>
</template>

<script setup>
import { onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import TopBar from './TopBar.vue'
import LeftNav from './LeftNav.vue'
import TabsBar from './TabsBar.vue'
import HelpPanel from './HelpPanel.vue'
import InitWizard from './InitWizard.vue'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { useTabsStore } from '@/stores/tabs'
import { findMenuByPath } from '@/business/menus'

const app = useAppStore()
const user = useUserStore()
const tabs = useTabsStore()
const route = useRoute()

onMounted(() => {
  if (app.dark) document.documentElement.classList.add('dark')
  // Login data is cached for fast startup; refresh it so server-side corrections
  // and permission changes replace stale localStorage values on the next load.
  Promise.allSettled([user.fetchUserInfo(), user.fetchFactories()])
  // T+ 行业化配置向导：首次登录自动弹出
  if (!app.initDone) app.openInitWizard()
})

watch(
  () => route.path,
  (p) => {
    tabs.setActive(p)
    const menu = findMenuByPath(p)
    if (menu && !tabs.tabs.find((t) => t.path === p)) tabs.open(menu)
  },
  { immediate: true }
)
</script>

<style scoped>
.portal {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.portal-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.portal-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.portal-content {
  flex: 1;
  overflow: auto;
  background: var(--t-content-bg);
  padding: 12px;
}

/* 移动端：抽屉遮罩 */
.nav-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 900;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 768px) {
  .portal-content {
    padding: 8px;
  }
}
</style>
