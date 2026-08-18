import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
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
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      // PanelX 平台反代：SDK/数据接口与页面同源。
      // preload 生产模式（非 localhost）会向上 ping /wp-core/api/ping 探测 baseURL，
      // 命中后 SDK 从本域同源加载 —— 局域网直连 / 内网穿透 / 正式部署 全靠这段代理。
      '/wp-core': {
        target: 'http://203.132.49.57:6612/hscx',
        changeOrigin: true,
      },
      '/wp-file': {
        target: 'http://203.132.49.57:6612/hscx',
        changeOrigin: true,
      },
    },
  },
  // vite preview（构建产物本地预览/共享时同样代理平台）
  preview: {
    host: true,
    port: 4173,
    proxy: {
      // 路线①（完全自建）本地验证：/api -> 本机 Spring Boot
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/wp-core': {
        target: 'http://203.132.49.57:6612/hscx',
        changeOrigin: true,
      },
      '/wp-file': {
        target: 'http://203.132.49.57:6612/hscx',
        changeOrigin: true,
      },
    },
  },
})
