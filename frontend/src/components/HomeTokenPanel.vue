<script setup>
/**
 * Token 统计卡片（HomeView 拆分 slice-5，2026-09-12）
 * - 组件内自加载 usage/history（onMounted）；expose refresh() 供父组件在
 *   AI 完成（onAiDone）与 keep-alive 切回（onActivated）时刷新
 * - tokenUsage 为空时不渲染卡片（原 HomeView v-if 语义）
 */
import { ref, onMounted } from 'vue'
import { tokenApi } from '../utils/api'
import { createFailureNotifier } from '../utils/errorNotice'

const tokenUsage = ref(null)
const tokenHistory = ref([])
const tokenPanelOpen = ref(false)
const tokenNotifier = createFailureNotifier('Token 统计拉取失败')

async function loadTokenUsage() {
  try {
    const res = await tokenApi.usage()
    tokenUsage.value = res
    tokenNotifier.ok()
  } catch {
    tokenNotifier.fail()
  }
}

async function loadTokenHistory() {
  try {
    const res = await tokenApi.history(30)
    tokenHistory.value = res.records || []
    tokenNotifier.ok()
  } catch {
    tokenNotifier.fail()
  }
}

function formatTokenTime(t) {
  if (!t) return ''
  const d = new Date(t)
  const now = new Date()
  const diffMs = now - d
  if (diffMs < 60000) return '刚刚'
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}分钟前`
  if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}小时前`
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

async function refresh() {
  await Promise.all([loadTokenUsage(), loadTokenHistory()])
}

onMounted(refresh)
defineExpose({ refresh })
</script>

<template>
  <el-card v-if="tokenUsage" class="wb-card" shadow="never">
    <template #header>
      <div class="card-head-row token-head" @click="tokenPanelOpen = !tokenPanelOpen">
        <span class="wb-card-title">Token 统计</span>
        <span class="card-head-hint">
          总计 {{ ((tokenUsage.total_input || 0) + (tokenUsage.total_output || 0)).toLocaleString() }} tokens
        </span>
        <el-icon class="token-chevron" :class="{ open: tokenPanelOpen }">
          <svg viewBox="0 0 1024 1024" width="14" height="14"><path d="M884 256h-75c-5.1 0-9.9 2.5-12.9 6.6L512 654.2 227.9 262.6c-3-4.1-7.8-6.6-12.9-6.6h-75c-6.5 0-10.3 7.4-6.5 12.7l352.6 486.1c12.8 17.6 39 17.6 51.7 0l352.6-486.1c3.9-5.3.1-12.7-6.4-12.7z" fill="currentColor"/></svg>
        </el-icon>
      </div>
    </template>

    <div v-show="tokenPanelOpen">
      <el-row :gutter="16" style="margin-bottom: 12px;">
        <el-col :span="8">
          <div class="token-stat-item">
            <div class="token-stat-label">输入 Token</div>
            <div class="token-stat-value">{{ (tokenUsage.total_input || 0).toLocaleString() }}</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="token-stat-item">
            <div class="token-stat-label">输出 Token</div>
            <div class="token-stat-value">{{ (tokenUsage.total_output || 0).toLocaleString() }}</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="token-stat-item">
            <div class="token-stat-label">总计 Token</div>
            <div class="token-stat-value token-stat-total">
              {{ ((tokenUsage.total_input || 0) + (tokenUsage.total_output || 0)).toLocaleString() }}
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- 按模型分列 -->
      <div v-if="tokenUsage.by_model && Object.keys(tokenUsage.by_model).length > 0">
        <el-divider content-position="left" style="font-size: 12px;">按模型</el-divider>
        <el-table :data="Object.entries(tokenUsage.by_model).map(([k,v]) => ({model: k, ...v}))" size="small" stripe>
          <el-table-column prop="model" label="模型" min-width="160" />
          <el-table-column prop="input_tokens" label="输入" width="100">
            <template #default="{ row }">{{ (row.input_tokens || 0).toLocaleString() }}</template>
          </el-table-column>
          <el-table-column prop="output_tokens" label="输出" width="100">
            <template #default="{ row }">{{ (row.output_tokens || 0).toLocaleString() }}</template>
          </el-table-column>
          <el-table-column label="合计" width="100">
            <template #default="{ row }">{{ ((row.input_tokens || 0) + (row.output_tokens || 0)).toLocaleString() }}</template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 调用历史 -->
      <div v-if="tokenHistory.length > 0">
        <el-divider content-position="left" style="font-size: 12px;">调用历史 (次数)</el-divider>
        <el-table :data="tokenHistory" size="small" stripe max-height="250">
          <el-table-column prop="created_at" label="时间" width="140">
            <template #default="{ row }">{{ formatTokenTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="model" label="模型" min-width="140" />
          <el-table-column prop="title" label="来源" min-width="120" show-overflow-tooltip />
          <el-table-column label="输入" width="80" align="right">
            <template #default="{ row }">{{ (row.input_tokens || 0).toLocaleString() }}</template>
          </el-table-column>
          <el-table-column label="输出" width="80" align="right">
            <template #default="{ row }">{{ (row.output_tokens || 0).toLocaleString() }}</template>
          </el-table-column>
          <el-table-column label="合计" width="80" align="right">
            <template #default="{ row }">
              <span style="color: #409eff; font-weight: 500;">{{ (row.total_tokens || 0).toLocaleString() }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div v-else-if="tokenPanelOpen" style="text-align: center; padding: 12px; color: #909399; font-size: 12px;">
        暂无调用记录
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.token-head {
  cursor: pointer;
  width: 100%;
}
.token-chevron {
  margin-left: auto;
  color: var(--app-text-secondary);
  transition: transform 0.2s;
}
.token-chevron.open {
  transform: rotate(180deg);
}
.token-stat-item {
  text-align: center;
  padding: 12px;
  background: var(--app-hover-bg);
  border: 1px solid var(--app-border);
  border-radius: 10px;
}
.token-stat-label {
  font-size: 12px;
  color: var(--app-text-secondary);
  margin-bottom: 6px;
}
.token-stat-value {
  font-size: 20px;
  font-weight: 600;
  color: var(--app-text-heading);
}
.token-stat-total {
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
</style>
