import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 后端端口与 application.yml 的 MES_HTTP_PORT 同源（默认回归提交约定 8080），U 盘多机环境只改环境变量
const backendOrigin = `http://localhost:${process.env.MES_HTTP_PORT || '8080'}`
// AI 助理（合并账号体系后挂在同源路径下）：页面与接口是两个不同进程
const aiDeskOrigin = `http://localhost:${process.env.AI_DESK_PORT || '18092'}`
const aiBackendOrigin = `http://localhost:${process.env.AI_API_PORT || '8000'}`

export default defineConfig({
  plugins: [vue()],
  build: {
    // 手动分包：把不常变的第三方库拆出来，让浏览器并行下载并长期缓存。
    // 原来入口包 1.4MB（Element Plus + Vue + 业务代码全在一起），
    // 任何业务改动都会让整个包失效、用户重新下载 1.4MB。
    // 拆开后：vendor 命中缓存，只重下几百 KB 的业务包。
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          // xlsx 必须留空字符串（=交给 Rollup 按动态 import 边界自己切），
          // 否则会被下面的兜底规则吸回 vendor，等于又进了首屏。
          if (id.includes('/xlsx/') || id.includes('\\xlsx\\')) return undefined
          if (id.includes('element-plus')) return 'vendor-element'
          if (id.includes('@element-plus/icons-vue')) return 'vendor-icons'
          if (id.includes('/vue/') || id.includes('@vue/') || id.includes('vue-router') || id.includes('pinia')) {
            return 'vendor-vue'
          }
          if (id.includes('vue-flow') || id.includes('dagre')) return 'vendor-flow'
          if (id.includes('axios')) return 'vendor-http'
          return 'vendor'
        },
      },
    },
    // 入口包体积告警阈值，避免每次构建都刷同样的 warning
    chunkSizeWarningLimit: 800,
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // 通用引擎层（跨项目可复用），业务层用 '@/business/...' 访问
      '@core': fileURLToPath(new URL('./src/core', import.meta.url)),
    },
  },
  server: {
    // 局域网可访问（同事浏览器访问 http://<本机IP>:5173；Windows 防火墙需放行 5173）
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: backendOrigin,
        changeOrigin: true,
      },
    },
  },
  // vite preview（构建产物本地预览/共享时同样代理平台）
  preview: {
    host: true,
    port: 4173,
    proxy: {
      '/api': {
        target: backendOrigin,
        changeOrigin: true,
      },
      // AI 助理并入 MES 同源：静态页面走 /ai，接口走 /ai-api。
      // /ai-api 必须排在 /ai 前面，否则会被 /ai 先吃掉。
      '/ai-api': {
        target: aiBackendOrigin,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/ai-api/, ''),
      },
      '/ai': {
        target: aiDeskOrigin,
        changeOrigin: true,
        // 工作台自身按根路径组织资源，挂到 /ai 下必须把前缀去掉，
        // 否则 /ai/css/desk.css 会被转成 :18092/ai/css/desk.css → 404
        rewrite: (path) => path.replace(/^\/ai(?=\/|$)/, '') || '/',
        // 前缀被 rewrite 掉了，18092 就分不清「这次是挂在 /ai 下」还是「直接访问根」，
        // 于是把 <base href> 写成 "/"，页面里的 js/main.js 解析成 /js/main.js → 撞上
        // MES 的 SPA 回落拿到 HTML → 模块不加载 → 整页只剩静态骨架。
        // 这里显式告诉它真实前缀，别再猜。
        headers: { 'X-Desk-Prefix': '/ai/' },
      },
    },
  },
})
