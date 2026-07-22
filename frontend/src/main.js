import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'

import App from './App.vue'

// ★ 主题模式由 themeStore 管理（data-theme 属性 + dark class）
// 不再硬编码 dark class，支持日间/夜间切换
import './stores/themeStore'

// 路由配置
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
    { path: '/result/:jobId', name: 'result', component: () => import('./views/ResultView.vue') },
    { path: '/history', name: 'history', component: () => import('./views/HistoryView.vue') },
    { path: '/models', name: 'models', component: () => import('./views/ModelsView.vue') },
    { path: '/templates', name: 'templates', component: () => import('./views/TemplatesView.vue') },
    { path: '/storage', name: 'storage', component: () => import('./views/StorageCleanupView.vue') },
  ],
})

// Pinia 状态管理
const pinia = createPinia()

// 创建 Vue 应用
const app = createApp(App)

app.use(router)
app.use(pinia)
app.use(ElementPlus, { locale: zhCn })  // 中文化 Element Plus

app.mount('#app')
