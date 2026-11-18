<!-- Assistant.vue — AI 助理工作台：直接嵌入同源 /ai/。
     合并账号体系后不再需要 SSO 票据：/ai/ 与 MES 同源，浏览器直接带 MES 登录态，
     AI 后端用同一密钥验 MES 的 JWT，因此这里既不取 ticket 也不需要二次登录。 -->
<template>
  <div class="ai-page" ref="pageRef">
    <div class="ai-page__bar">
      <div class="ai-page__title">
        <el-icon class="ai-page__icon"><MagicStick /></el-icon>
        <span>{{ tt('AI 助理') }}</span>
        <span class="ai-page__hint">{{ tt('今日简报、待办与对话都在这里，与 MES 同一账号') }}</span>
      </div>
      <div class="ai-page__acts">
        <el-button size="small" @click="reload">{{ tt('刷新') }}</el-button>
        <el-button size="small" type="primary" @click="openExternal">{{ tt('新标签打开') }}</el-button>
      </div>
    </div>

    <div class="ai-page__frame-wrap">
      <div v-if="unavailable" class="ai-page__missing">
        {{ tt('AI 工作台未部署（/ai/），当前环境暂不可用。') }}
      </div>
      <iframe v-else ref="frameRef" class="ai-page__frame" :src="src" :title="tt('AI 助理')" allow="microphone" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'

const { t: tt } = useI18n()
const frameRef = ref(null)
const pageRef = ref(null)
const unavailable = ref(false)
let onResize = null

/**
 * 高度按实际位置算，不用 100dvh 减常数。
 *
 * 外壳是「顶栏 + 页签栏 + 内容内边距」，各主题/缩放下具体像素会变；
 * 固定减法总会差几十像素，底部留一条空白（2026-11-17 实测差 43px）。
 */
function fitHeight() {
  const el = pageRef.value
  if (!el) return
  const top = el.getBoundingClientRect().top
  const pad = 24 // 内容区底部留白，与 .view 的 padding 对齐
  el.style.height = Math.max(360, window.innerHeight - top - pad) + 'px'
}

// 同源路径：由 vite（开发/预览）或 nginx（生产）反代到 AI 工作台进程。
// 带 embed=1：工作台自身的顶栏（搜索/主题/设置/用户）与 MES 顶栏重复，让它隐藏。
const src = ref('/ai/?embed=1')

function reload() {
  // 用重新赋 src 的方式强制重载，iframe 的 location.reload 跨文档不可靠
  const url = new URL(src.value, window.location.origin).toString()
  src.value = 'about:blank'
  setTimeout(() => { src.value = url }, 30)
}

function openExternal() {
  // 新标签是独立页面，不需要 embed（此时它才是唯一外层）
  window.open(new URL('/ai/', window.location.origin).toString(), '_blank', 'noopener,noreferrer')
}

onMounted(async () => {
  // /ai/ 未部署时 nginx 会回退成 MES 自己的 index.html，iframe 会无限自我嵌套；
  // 先探测一次，回退页或 404 都按「未部署」处理。
  try {
    const res = await fetch('/ai/?embed=1', { headers: { Accept: 'text/html' } })
    const text = await res.text()
    unavailable.value = !res.ok || text.includes('id="app"') || text.includes('<title>轻MES')
  } catch (e) {
    unavailable.value = true
  }
  // 首屏与窗口变化都要重算：外壳高度会随页签栏/缩放变化
  fitHeight()
  onResize = () => fitHeight()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  if (onResize) window.removeEventListener('resize', onResize)
  onResize = null
})
</script>

<style scoped>
/* 满屏嵌一个同源子应用：只保留一条 32px 操作条，其余全给 iframe。
   卡片 0 层用边框不用阴影；圆角 6；间距 8 的倍数。 */
.ai-page {
  display: flex;
  flex-direction: column;
  /* 外壳占位：顶栏 56 + 页签栏 40 + 内容区上下内边距，实测合计 146。
     少算会让 iframe 底部被顶出屏幕（2026-11-17 实测差 11px）。 */
  --ai-shell-offset: 146px;
  height: calc(100dvh - var(--ai-shell-offset));
  min-height: 360px;
  background: var(--t-card-bg);
  border: 1px solid var(--t-border);
  border-radius: 6px;
  overflow: hidden;
}
.ai-page__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--t-border-light);
  flex: none;
}
.ai-page__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--t-text-1);
}
.ai-page__icon { color: var(--t-primary); font-size: 16px; }
.ai-page__hint { font-size: 12px; color: var(--t-text-3); }
.ai-page__acts { display: flex; gap: 8px; }
.ai-page__missing {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #8c8c8c;
  font-size: 13px;
  border: 1px dashed #e5e5e5;
  border-radius: 6px;
  background: #fafafa;
}
.ai-page__frame-wrap { position: relative; flex: 1; min-height: 0; }
.ai-page__frame { width: 100%; height: 100%; border: 0; display: block; }
</style>
