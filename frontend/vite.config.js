import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    // Element Plus 自动导入（不需要手动 import 每个组件）
    AutoImport({
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      resolvers: [ElementPlusResolver()],
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      // 开发时前端请求代理到后端（用 127.0.0.1 而非 localhost：Node 会把 localhost 解析为 ::1，
      // 后端监听的是 IPv4，代理会连不上）
      '/api': {
        target: 'http://127.0.0.1:7001',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://127.0.0.1:7001',
        changeOrigin: true,
        ws: true,  // WebSocket proxy
      },
    },
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
  base: '/',
})
