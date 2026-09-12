<script setup>
/**
 * 系统日志面板（后端文件日志，默认收起）
 * 2026-09-12 从 HomeView 拆出（HomeView 拆分 slice-1）：useBackendLogs 自管理
 * 增量拉取/全量回退/轮询生命周期，组件内自持即可，无需父组件传参。
 * 根元素保留 id="log-panel"（scrollLogTop 依赖）。
 */
import { useBackendLogs } from '../composables/useBackendLogs'

const {
  showBackendLogs, backendLogs, autoScroll,
  loadAllHistory, clearLogs, scrollLogTop, refreshLogs,
} = useBackendLogs()
</script>

<template>
  <el-card class="wb-card" shadow="never">
    <template #header>
      <div class="card-head-row">
        <span class="wb-card-title">系统日志</span>
        <div class="card-head-actions">
          <el-switch v-if="showBackendLogs" v-model="autoScroll" active-text="自动滚动" size="small" />
          <el-button v-if="showBackendLogs" size="small" text @click="loadAllHistory">全部</el-button>
          <el-button v-if="showBackendLogs" size="small" text @click="clearLogs">清空</el-button>
          <el-button v-if="showBackendLogs" size="small" text @click="refreshLogs">刷新</el-button>
          <el-button size="small" text @click="showBackendLogs = !showBackendLogs">
            {{ showBackendLogs ? '收起' : '展开' }}
          </el-button>
        </div>
      </div>
    </template>
    <div v-show="showBackendLogs" id="log-panel" class="log-panel backend-log-panel">
      <div
        v-for="(line, i) in backendLogs"
        :key="i"
        class="backend-log-line"
        :class="{
          err: line.includes('[ERROR]'),
          warn: line.includes('[WARNING]'),
        }"
      >{{ line }}</div>
      <div class="log-panel-foot">
        <el-button size="small" text @click="scrollLogTop">返回顶部</el-button>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.backend-log-panel {
  max-height: 300px;
  white-space: pre-wrap;
  word-break: break-all;
}
.backend-log-line {
  color: var(--app-log-text);
  border-left: 3px solid transparent;
  padding-left: 6px;
}
.backend-log-line.err {
  color: #f56c6c;
  border-left-color: #f56c6c;
}
.backend-log-line.warn {
  color: #e6a23c;
  border-left-color: #e6a23c;
}
.log-panel-foot {
  text-align: right;
  margin-top: 4px;
}
</style>
