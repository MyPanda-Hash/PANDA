import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 后端端口与 application.yml 的 MES_HTTP_PORT 同源（默认回归提交约定 8080），U 盘多机环境只改环境变量
const backendOrigin = `http://localhost:${process.env.MES_HTTP_PORT || '8080'}`

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
    },
  },
})
