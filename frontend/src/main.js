import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
// Element Plus 组件/样式按需自动导入（vite.config.js 的 ElementPlusResolver）；
// 暗色变量仍全局引入，供主题切换
import 'element-plus/theme-chalk/dark/css-vars.css'
// JS API（ElMessage/ElMessageBox 以函数调用，不经模板解析器）的样式需显式引入
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'

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
// 组件级按需注册由 unplugin-vue-components 处理；语言包经 App.vue 的
// el-config-provider 全局注入，无需 app.use(ElementPlus)

app.mount('#app')
