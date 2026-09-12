<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CircleCheck, Clock, Trash2, Copy, Files, List, Lock, RefreshCw,
  Search, TriangleAlert,
} from 'lucide-vue-next'
import { storageCleanupApi } from '../utils/api'
import { isDialogCancel } from '../utils/errorNotice'

const policy = reactive({
  staged_ttl_hours: 72,
  orphan_ttl_days: 14,
  export_ttl_days: 7,
  media_ttl_days: 7,
  quarantine_ttl_days: 30,
  prune_url_audio: false,
  max_items: 1000,
  max_bytes: 20 * 1024 ** 3,
})
const maxBytesGiB = ref(20)
const loading = ref(false)
const state = ref({
  phase: 'idle',
  plan_id: null,
  plan_expires_at: null,
  plan: null,
  policy: null,
  progress: { current: 0, total: 0, percent: 0, bytes_reclaimed: 0 },
  results: [],
  logs: [],
  audit: [],
  active_jobs: 0,
  error: null,
})
let pollTimer = null

const phaseLabels = {
  idle: '未扫描', previewing: '扫描中', ready: '待确认', running: '执行中',
  completed: '已完成', failed: '失败', expired: '已过期',
}
const phaseType = computed(() => ({
  idle: 'info', previewing: 'warning', ready: 'success', running: 'warning',
  completed: 'success', failed: 'danger', expired: 'info',
}[state.value.phase] || 'info'))
const plan = computed(() => state.value.plan || { candidates: [], protected: [], counts: {}, bytes_by_category: {} })
const candidates = computed(() => plan.value.candidates || [])
const protectedPaths = computed(() => plan.value.protected || [])
const isBusy = computed(() => ['previewing', 'running'].includes(state.value.phase) || loading.value)
const canApply = computed(() => state.value.phase === 'ready' && !!state.value.plan_id && !state.value.active_jobs)
const categoryRows = computed(() => Object.entries(plan.value.counts || {}).map(([category, count]) => ({
  category,
  label: categoryLabel(category),
  count,
  bytes: plan.value.bytes_by_category?.[category] || 0,
})))

function categoryLabel(category) {
  return {
    staged_unreferenced: '未引用暂存上传',
    staged_completed_duplicate: '已完成任务重复上传',
    orphan_job_dir: '孤儿任务目录',
    expired_export: '过期导出',
    expired_url_audio: '过期 URL 音频',
    quarantine_expired: '过期隔离项',
  }[category] || category
}

function formatBytes(bytes) {
  const value = Number(bytes || 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}

function formatDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function syncState(payload) {
  state.value = payload
  if (payload.policy) {
    Object.assign(policy, payload.policy)
    maxBytesGiB.value = Number(payload.policy.max_bytes || policy.max_bytes) / 1024 ** 3
  }
  if (['previewing', 'running'].includes(payload.phase)) startPolling()
  else stopPolling()
}

async function loadStatus() {
  try {
    syncState(await storageCleanupApi.status())
  } catch (error) {
    ElMessage.error(`读取存储状态失败：${error.message}`)
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = window.setInterval(loadStatus, 1200)
}

function stopPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = null
}

async function preview() {
  loading.value = true
  try {
    policy.max_bytes = Math.max(1, Number(maxBytesGiB.value || 1)) * 1024 ** 3
    syncState(await storageCleanupApi.preview({ ...policy }))
    ElMessage.success('预览已生成，未修改任何文件')
  } catch (error) {
    ElMessage.error(`预览失败：${error.message}`)
  } finally {
    loading.value = false
  }
}

async function applyCleanup() {
  try {
    const result = await ElMessageBox.prompt(
      '此操作将按当前预览处理候选项。输入 DELETE 继续。',
      '确认执行存储清理',
      {
        confirmButtonText: '执行清理',
        cancelButtonText: '取消',
        inputPlaceholder: 'DELETE',
        inputPattern: /^DELETE$/,
        inputErrorMessage: '必须输入 DELETE',
        type: 'warning',
      },
    )
    loading.value = true
    await storageCleanupApi.apply(state.value.plan_id, result.value)
    await loadStatus()
    ElMessage.success('清理已开始，进度会自动刷新')
  } catch (error) {
    if (!isDialogCancel(error)) ElMessage.error(`执行失败：${error?.message || error}`)
  } finally {
    loading.value = false
  }
}

onMounted(loadStatus)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class='storage-page'>
    <header class='storage-header'>
      <div>
        <div class='eyebrow'><Files /> 存储管理</div>
        <h1>清理工作台</h1>
        <p>先预览，再确认执行。所有候选项都会在实际处理前重新校验。</p>
      </div>
      <div class='header-actions'>
        <el-tag :type='phaseType' effect='plain' size='large'>{{ phaseLabels[state.phase] || state.phase }}</el-tag>
        <el-button :loading='loading' :disabled='isBusy' @click='loadStatus'>
          <el-icon><RefreshCw /></el-icon>刷新状态
        </el-button>
      </div>
    </header>

    <section class='policy-section'>
      <div class='section-heading'>
        <div>
          <h2>清理策略</h2>
          <span>阈值只影响本次预览，不会自动执行。</span>
        </div>
        <el-button type='primary' :loading='loading' :disabled='isBusy' @click='preview'>
          <el-icon><Search /></el-icon>生成预览
        </el-button>
      </div>
      <div class='policy-grid'>
        <label><span>暂存上传保留（小时）</span><el-input-number v-model='policy.staged_ttl_hours' :min='0' :max='8760' controls-position='right' /></label>
        <label><span>孤儿目录保留（天）</span><el-input-number v-model='policy.orphan_ttl_days' :min='0' :max='3650' controls-position='right' /></label>
        <label><span>导出文件保留（天）</span><el-input-number v-model='policy.export_ttl_days' :min='0' :max='3650' controls-position='right' /></label>
        <label><span>媒体保留（天）</span><el-input-number v-model='policy.media_ttl_days' :min='0' :max='3650' controls-position='right' /></label>
        <label><span>隔离区保留（天）</span><el-input-number v-model='policy.quarantine_ttl_days' :min='0' :max='3650' controls-position='right' /></label>
        <label><span>最大候选项数</span><el-input-number v-model='policy.max_items' :min='1' :max='100000' controls-position='right' /></label>
        <label><span>单次预算（GiB）</span><el-input-number v-model='maxBytesGiB' :min='1' :max='10240' :step='1' controls-position='right' /></label>
        <label class='toggle-field'><span>允许清理 URL 音频</span><el-switch v-model='policy.prune_url_audio' /></label>
      </div>
      <div v-if='policy.prune_url_audio' class='policy-warning'><el-icon><TriangleAlert /></el-icon>URL 音频清理已启用，仅会处理已有字幕且超过阈值的音频。</div>
    </section>

    <section class='summary-grid' aria-label='预览摘要'>
      <div class='summary-item'><span>候选项</span><strong>{{ plan.candidate_count || 0 }}</strong><small>项</small></div>
      <div class='summary-item accent-green'><span>预计释放</span><strong>{{ formatBytes(plan.total_bytes) }}</strong><small>{{ plan.total_bytes || 0 }} bytes</small></div>
      <div class='summary-item'><span>受保护路径</span><strong>{{ protectedPaths.length }}</strong><small>项</small></div>
      <div class='summary-item accent-amber'><span>活跃任务</span><strong>{{ state.active_jobs || 0 }}</strong><small>{{ state.active_jobs ? '执行期间暂不可清理' : '可以执行清理' }}</small></div>
    </section>

    <section class='operation-bar'>
      <div class='plan-meta'>
        <el-icon><Lock /></el-icon>
        <div>
          <strong>{{ state.plan_id ? `计划 ${state.plan_id.slice(0, 10)}` : '尚无可执行计划' }}</strong>
          <span v-if='state.plan_expires_at'>有效期至 {{ formatDate(state.plan_expires_at * 1000) }}</span>
          <span v-else>生成新预览后才能执行</span>
        </div>
      </div>
      <el-button type='danger' :disabled='!canApply || !candidates.length' :loading='state.phase === "running"' @click='applyCleanup'>
        <el-icon><Trash2 /></el-icon>确认并执行
      </el-button>
    </section>

    <el-alert v-if='state.error' :title='state.error' type='error' :closable='false' show-icon />

    <section v-if='state.phase === "running" || state.results?.length' class='progress-section'>
      <div class='section-heading compact'>
        <div>
          <h2>执行进度</h2>
          <span>{{ state.progress.current }} / {{ state.progress.total }} 项，已释放 {{ formatBytes(state.progress.bytes_reclaimed) }}</span>
        </div>
        <strong>{{ state.progress.percent }}%</strong>
      </div>
      <el-progress :percentage='state.progress.percent || 0' :status='state.phase === "completed" ? "success" : undefined' />
    </section>

    <section class='workspace-section'>
      <el-tabs class='storage-tabs'>
        <el-tab-pane name='candidates'>
          <template #label><el-icon><List /></el-icon><span>候选项 {{ candidates.length }}</span></template>
          <div v-if='categoryRows.length' class='category-strip'>
            <div v-for='row in categoryRows' :key='row.category'>
              <span>{{ row.label }}</span><strong>{{ row.count }}</strong><small>{{ formatBytes(row.bytes) }}</small>
            </div>
          </div>
          <el-empty v-if='!candidates.length' description='当前没有清理候选项' :image-size='72' />
          <el-table v-else :data='candidates' stripe height='440'>
            <el-table-column label='类别' width='180'><template #default='{ row }'>{{ categoryLabel(row.category) }}</template></el-table-column>
            <el-table-column prop='action' label='动作' width='100'><template #default='{ row }'><el-tag :type='row.action === "quarantine" ? "warning" : "danger"' effect='plain'>{{ row.action === 'quarantine' ? '隔离' : '删除' }}</el-tag></template></el-table-column>
            <el-table-column label='大小' width='110'><template #default='{ row }'>{{ formatBytes(row.size) }}</template></el-table-column>
            <el-table-column prop='path' label='路径' min-width='280' show-overflow-tooltip />
            <el-table-column prop='reason' label='判定依据' min-width='260' show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <el-tab-pane name='protected'>
          <template #label><el-icon><Lock /></el-icon><span>受保护 {{ protectedPaths.length }}</span></template>
          <el-empty v-if='!protectedPaths.length' description='没有额外的受保护路径记录' :image-size='72' />
          <el-table v-else :data='protectedPaths' stripe height='440'>
            <el-table-column prop='path' label='路径' min-width='360' show-overflow-tooltip />
            <el-table-column prop='reason' label='保护原因' min-width='320' show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <el-tab-pane name='results'>
          <template #label><el-icon><CircleCheck /></el-icon><span>执行结果 {{ state.results?.length || 0 }}</span></template>
          <el-empty v-if='!state.results?.length' description='尚无执行结果' :image-size='72' />
          <el-table v-else :data='state.results' stripe height='440'>
            <el-table-column prop='status' label='状态' width='120'><template #default='{ row }'><el-tag :type='row.status === "skipped" ? "warning" : "success"'>{{ row.status }}</el-tag></template></el-table-column>
            <el-table-column label='回收空间' width='130'><template #default='{ row }'>{{ formatBytes(row.bytes_reclaimed) }}</template></el-table-column>
            <el-table-column prop='path' label='路径' min-width='340' show-overflow-tooltip />
            <el-table-column prop='detail' label='详情' min-width='260' show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <el-tab-pane name='audit'>
          <template #label><el-icon><Copy /></el-icon><span>审计 {{ state.audit?.length || 0 }}</span></template>
          <el-empty v-if='!state.audit?.length' description='尚无清理审计记录' :image-size='72' />
          <el-table v-else :data='[...state.audit].reverse()' stripe height='440'>
            <el-table-column label='时间' width='180'><template #default='{ row }'>{{ formatDate(row.timestamp) }}</template></el-table-column>
            <el-table-column prop='event' label='事件' width='170' />
            <el-table-column prop='status' label='状态' width='120' />
            <el-table-column prop='path' label='对象' min-width='320' show-overflow-tooltip />
            <el-table-column label='回收空间' width='130'><template #default='{ row }'>{{ formatBytes(row.bytes_reclaimed) }}</template></el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </section>

    <section class='logs-section'>
      <div class='section-heading compact'>
        <div><h2>操作日志</h2><span>{{ state.logs?.length || 0 }} 条当前会话记录</span></div>
        <el-icon><Clock /></el-icon>
      </div>
      <div class='log-console'>
        <div v-if='!state.logs?.length' class='log-empty'>等待操作</div>
        <div v-for='(entry, index) in state.logs' :key='`${entry.timestamp}-${index}`' :class='["log-line", entry.level]'>
          <time>{{ formatDate(entry.timestamp) }}</time><span>{{ entry.message }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.storage-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
  box-sizing: border-box;
  color: var(--app-text);
}

.storage-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  padding: 18px 0 22px;
  border-bottom: 1px solid var(--app-border);
}

.storage-header h1 {
  margin: 5px 0 7px;
  font-size: 26px;
  line-height: 1.25;
  letter-spacing: 0;
}

.storage-header p,
.section-heading span {
  margin: 0;
  color: var(--app-text-secondary);
  font-size: 13px;
}

.eyebrow {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--app-accent-1, #409eff);
  font-size: 12px;
  font-weight: 700;
}

.eyebrow svg { width: 15px; }
.header-actions { display: flex; align-items: center; gap: 10px; }

.policy-section,
.workspace-section,
.logs-section,
.progress-section {
  padding: 22px;
  margin-bottom: 16px;
  border: 1px solid var(--app-border);
  border-radius: 12px;
  background: rgba(var(--app-dim-color), var(--app-card-opacity, 1));
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: box-shadow 0.2s, border-color 0.2s;
}
.policy-section:hover,
.workspace-section:hover,
.logs-section:hover,
.progress-section:hover {
  box-shadow: var(--app-card-glow, 0 8px 28px rgba(0, 0, 0, 0.42));
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 18px;
}

.section-heading.compact { margin-bottom: 12px; }
.section-heading h2 { margin: 0 0 3px; font-size: 16px; letter-spacing: 0; }

.policy-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(180px, 1fr));
  gap: 14px 18px;
}

.policy-grid label {
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
  color: var(--app-text-secondary);
  font-size: 12px;
}

.policy-grid :deep(.el-input-number) { width: 100%; }
.policy-grid .toggle-field { justify-content: space-between; }
.policy-grid .toggle-field :deep(.el-switch) { align-self: flex-start; height: 32px; }

.policy-warning {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  padding: 10px 12px;
  border-left: 3px solid #e6a23c;
  background: color-mix(in srgb, #e6a23c 10%, transparent);
  color: #b88230;
  font-size: 12px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border-bottom: 1px solid var(--app-border);
}

.summary-item {
  position: relative;
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: end;
  min-height: 92px;
  padding: 18px 20px;
  box-sizing: border-box;
  border-right: 1px solid var(--app-border);
}

.summary-item:last-child { border-right: 0; }
.summary-item::before { content: ''; position: absolute; left: 0; top: 17px; bottom: 17px; width: 3px; background: var(--app-accent-1, #409eff); }
.summary-item.accent-green::before { background: #34a853; }
.summary-item.accent-amber::before { background: #e6a23c; }
.summary-item span { grid-column: 1 / -1; color: var(--app-text-secondary); font-size: 12px; }
.summary-item strong { font-size: 23px; line-height: 1.2; letter-spacing: 0; }
.summary-item small { color: var(--app-text-secondary); font-size: 11px; text-align: right; }

.operation-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 16px 0;
  border-bottom: 1px solid var(--app-border);
}

.plan-meta { display: flex; align-items: center; gap: 11px; min-width: 0; }
.plan-meta > .el-icon { flex: 0 0 auto; color: #67c23a; font-size: 19px; }
.plan-meta div { display: flex; flex-direction: column; min-width: 0; }
.plan-meta strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; }
.plan-meta span { margin-top: 2px; color: var(--app-text-secondary); font-size: 11px; }

.storage-tabs :deep(.el-tabs__header) { margin-bottom: 16px; }
.storage-tabs :deep(.el-tabs__item) { gap: 6px; }
.storage-tabs :deep(.el-table) { border: 1px solid var(--app-border); border-radius: 6px; overflow: hidden; }

.category-strip {
  display: flex;
  gap: 1px;
  margin-bottom: 14px;
  overflow-x: auto;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  background: var(--app-border);
}

.category-strip > div {
  display: grid;
  grid-template-columns: auto auto;
  gap: 3px 14px;
  flex: 1 0 175px;
  padding: 10px 12px;
  background: var(--app-card-bg);
}

.category-strip span { grid-column: 1 / -1; color: var(--app-text-secondary); font-size: 11px; }
.category-strip strong { font-size: 16px; }
.category-strip small { align-self: end; color: var(--app-text-secondary); text-align: right; }

.logs-section .section-heading > .el-icon { color: var(--app-text-secondary); font-size: 18px; }

.log-console {
  height: 230px;
  overflow: auto;
  padding: 11px 13px;
  box-sizing: border-box;
  border: 1px solid var(--app-log-border);
  border-radius: 6px;
  background: var(--app-log-bg);
  color: var(--app-log-text);
  font: 12px/1.7 ui-monospace, SFMono-Regular, Consolas, monospace;
}

.log-line { display: grid; grid-template-columns: 165px minmax(0, 1fr); gap: 12px; }
.log-line time { color: var(--app-log-time); white-space: nowrap; }
.log-line.error span { color: #f56c6c; }
.log-empty { color: var(--app-text-secondary); }

@media (max-width: 1100px) {
  .policy-grid { grid-template-columns: repeat(2, minmax(180px, 1fr)); }
  .summary-grid { grid-template-columns: repeat(2, 1fr); }
  .summary-item:nth-child(2) { border-right: 0; }
  .summary-item:nth-child(-n + 2) { border-bottom: 1px solid var(--app-border); }
}

@media (max-width: 700px) {
  .storage-page { padding: 12px; }
  .storage-header { flex-direction: column; gap: 14px; padding-top: 8px; }
  .storage-header h1 { font-size: 22px; }
  .header-actions { width: 100%; justify-content: space-between; }
  .policy-section, .workspace-section, .logs-section, .progress-section { padding: 16px; }
  .section-heading { align-items: flex-end; }
  .policy-grid { grid-template-columns: 1fr; }
  .summary-grid { grid-template-columns: 1fr 1fr; }
  .summary-item { min-height: 84px; padding: 14px 12px 14px 16px; }
  .summary-item strong { font-size: 18px; }
  .operation-bar { align-items: stretch; flex-direction: column; }
  .operation-bar :deep(.el-button) { width: 100%; }
  .storage-tabs :deep(.el-tabs__nav-wrap) { overflow-x: auto; }
  .storage-tabs :deep(.el-tabs__item) { padding: 0 11px; }
  .log-line { grid-template-columns: 1fr; gap: 0; padding-bottom: 5px; }
}
</style>
