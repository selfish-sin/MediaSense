import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  test: {
    globals: true,
    environment: 'jsdom',
    // ★ 固定套件时区：时间类断言（chatExport 的 HH:mm）按 Intl 本地时区渲染，这是产品行为本身；
    //   不固定则开发机（+08）与 CI runner（UTC）各得一个结果——2026-10-13 CI 就红在这条上。
    //   选 +08 而非 UTC：UTC 下 03:20Z 与本地 03:20 同值，反而测不出「naive UTC 被当本地时间」的偏移回归。
    env: { TZ: 'Asia/Shanghai' },
    server: {
      deps: {
        inline: ['element-plus'],
      },
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      exclude: [
        'node_modules/',
        'dist/',
        '**/*.config.js',
        'src/main.js',
        'src/components/HelloWorld.vue',
      ],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
