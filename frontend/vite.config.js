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
      // 开发时前端请求代理到后端
      '/api': {
        target: 'http://localhost:7000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://localhost:7000',
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
