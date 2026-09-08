<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Eye, Trash2, Search } from 'lucide-vue-next'
import { jobsApi, exportApi } from '../utils/api'
import {
  ACTIVE_JOB_STATUSES,
  buildRerunRoute,
  buildRestoreRoute,
  getJobActionModel,
} from '../utils/jobActions'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'

const router = useRouter()

const jobs = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const selectedJobs = ref([])  // 多选勾中的任务

async function loadJobs() {
  loading.value = true
  try {
    const params = { limit: pageSize.value, offset: (page.value - 1) * pageSize.value }
    // ★ 关键词过滤：按标题/URL 过滤列表（后端 get_jobs 的 keyword 参数）
    const q = searchQuery.value.trim()
    if (q) params.keyword = q
    const res = await jobsApi.list(params)
    jobs.value = res.jobs || []
    total.value = res.total || 0
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

async function handleDelete(jobId) {
  try {
    // 先查询磁盘信息
    let diskMsg = ''
    try {
      const info = await jobsApi.batchDiskInfo([jobId])
      if (info.total_size_bytes > 0) {
        diskMsg = `\n\n磁盘文件：${info.jobs[0]?.job_dir || ''}\n大小：${info.total_size_desc}`
      } else {
        diskMsg = '\n\n（无磁盘文件）'
      }
    } catch {}

    await ElMessageBox.confirm(
      `确认删除此任务？` + diskMsg,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
    await jobsApi.delete(jobId, true)
    ElMessage.success('已删除（含磁盘文件）')
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error(err.message)
    }
  }
}

async function handleClearAll() {
  try {
    await ElMessageBox.confirm('确认清空所有任务？此操作不可恢复！', '警告', { type: 'error' })
    await jobsApi.clear()
    ElMessage.success('已清空')
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error(err.message)
    }
  }
}

async function handleClearByStatus(status, label) {
  try {
    await ElMessageBox.confirm(`确认清空所有「${label}」任务？此操作不可恢复！`, '警告', { type: 'error' })
    const res = await jobsApi.clearByStatus(status)
    ElMessage.success(res.message || '已清空')
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error(err.message)
    }
  }
}

// ★ 清空失败 + 取消的任务（用户要求一并清理）
async function handleClearFailed() {
  try {
    await ElMessageBox.confirm('确认清空所有「失败/取消」任务？此操作不可恢复！', '警告', { type: 'error' })
    const [r1, r2] = await Promise.all([
      jobsApi.clearByStatus('failed'),
      jobsApi.clearByStatus('cancelled'),
    ])
    const total = (r1.data?.count || 0) + (r2.data?.count || 0)
    ElMessage.success(`已清空 ${total} 个失败/取消任务`)
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error(err.message)
    }
  }
}

function handlePageChange(p) {
  page.value = p
  selectedJobs.value = []
  loadJobs()
}

function viewResult(jobId) {
  router.push(`/result/${jobId}`)
}

function actionFor(job) {
  return getJobActionModel(job.status)
}

async function handlePrimaryAction(job) {
  const action = actionFor(job).primary
  if (action === 'restore') {
    await router.push(buildRestoreRoute(job.id))
    return
  }
  if (action === 'result' || action === 'detail') {
    viewResult(job.id)
    return
  }
  if (action === 'resume') {
    await handleRetry(job.id, false)
  }
}

async function handleSecondaryAction(job) {
  const action = actionFor(job).secondary
  if (action === 'rerun') {
    await router.push(buildRerunRoute(job.id))
    return
  }
  if (action === 'forceRetry') {
    await handleRetry(job.id, true)
  }
}

function formatTime(time) {
  if (!time) return ''
  return new Date(time).toLocaleString()
}

// ============ 历史列表关键词过滤 ============
const searchQuery = ref('')
const searching = ref(false)

async function doSearch() {
  const q = searchQuery.value.trim()
  if (!q) { return }
  searching.value = true
  try {
    page.value = 1  // ★ 搜索时回到第一页
    await loadJobs()
  } catch (err) {
    ElMessage.error('搜索失败: ' + err.message)
  } finally {
    searching.value = false
  }
}

function clearSearch() {
  searchQuery.value = ''
  page.value = 1
  loadJobs()
}

// ============ 重试 ============
// ★ 重试后跳转到工作台，让用户看到处理进度（不是后台偷偷跑）
async function handleRetry(jobId, force = false) {
  try {
    if (force) {
      await ElMessageBox.confirm('从头重试会删除该任务已有文件并完全重新处理。确认？', '从头重试', { type: 'warning' })
    }
    await jobsApi.retry(jobId, force)
    ElMessage.success(force ? '已从头重新处理，正在打开工作台' : '已断点续传，正在打开工作台')
    await router.push(buildRestoreRoute(jobId))
  } catch (err) {
    if (err !== 'cancel') ElMessage.error(err.message)
  }
}

// ============ 彻底取消 ============
const activeStatuses = ACTIVE_JOB_STATUSES

async function handleCancel(jobId) {
  try {
    await ElMessageBox.confirm(
      '彻底取消将终止后端所有处理步骤（下载/转录/AI总结），任务状态变为"已取消"。确认？',
      '彻底取消任务',
      { type: 'warning', confirmButtonText: '确认取消', cancelButtonText: '算了' }
    )
    await jobsApi.cancel(jobId)
    ElMessage.success('任务已取消')
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error(err.message)
  }
}

// ============ 多选 + 批量操作 ============
function handleSelectionChange(selection) {
  selectedJobs.value = selection
}

function _formatBytes(bytesVal) {
  if (!bytesVal || bytesVal <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = bytesVal
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(1) + ' ' + units[i]
}

async function handleBatchCancel() {
  const running = selectedJobs.value.filter(j => activeStatuses.includes(j.status))
  if (running.length === 0) {
    ElMessage.warning('选中的任务中没有正在运行的')
    return
  }
  const others = selectedJobs.value.filter(j => !activeStatuses.includes(j.status))
  let extraMsg = ''
  if (others.length > 0) {
    extraMsg = `\n（另外 ${others.length} 个非运行中状态的任务将跳过）`
  }
  try {
    await ElMessageBox.confirm(
      `将取消 ${running.length} 个正在运行的任务，终止所有后端处理步骤。确认？` + extraMsg,
      '批量取消',
      { type: 'warning', confirmButtonText: '确认取消', cancelButtonText: '算了' }
    )
    const ids = running.map(j => j.id)
    const res = await jobsApi.batchCancel(ids)
    ElMessage.success(`已取消 ${res.cancelled_count} 个任务`)
    selectedJobs.value = []
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error(err.message)
  }
}

async function handleBatchExport() {
  // 只导出已完成且有结果的任务；其余状态由后端兜底跳过，不会报错
  const exportable = selectedJobs.value.filter(j => j.status === 'completed')
  if (exportable.length === 0) {
    ElMessage.warning('选中的任务中没有已完成且可导出结果的任务')
    return
  }
  try {
    const res = await exportApi.batch(exportable.map(j => j.id))
    ElMessage.success(`已导出 ${exportable.length} 个任务的结果（${res.filename}）`)
  } catch (err) {
    ElMessage.error('导出失败: ' + err.message)
  }
}

async function handleBatchDelete() {
  if (selectedJobs.value.length === 0) return
  const ids = selectedJobs.value.map(j => j.id)

  // 1. 先 dry_run 获取磁盘信息
  let diskInfo = null
  try {
    diskInfo = await jobsApi.batchDiskInfo(ids)
  } catch (err) {
    ElMessage.error('获取磁盘信息失败: ' + err.message)
    return
  }

  // 2. 构建带文件详情的确认消息
  const jobItems = diskInfo.jobs || []
  let detailLines = jobItems.map(j => {
    const title = (j.title || j.job_id).substring(0, 40)
    const size = _formatBytes(j.size_bytes)
    const status = j.exists ? '' : ' [任务不存在]'
    return `· ${title}${status} —— ${size}`
  }).join('\n')

  const hasFileJobs = jobItems.filter(j => j.size_bytes > 0)
  let summaryLine = ''
  if (hasFileJobs.length > 0) {
    summaryLine = `\n共 ${hasFileJobs.length} 个任务有磁盘文件，总计 ${_formatBytes(diskInfo.total_size_bytes)}`
  }

  try {
    await ElMessageBox.confirm(
      `确认删除 ${ids.length} 个任务？此操作不可恢复！\n\n` +
      detailLines +
      summaryLine,
      '批量删除确认',
      {
        type: 'error',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        dangerouslyUseHTMLString: false,
        // 消息可能很长，用自定义样式
        customClass: 'batch-delete-dialog',
      }
    )
    // 3. 真删
    const res = await jobsApi.batchDelete(ids, false, true)
    ElMessage.success(res.message || `已删除 ${res.deleted_count} 个任务`)
    selectedJobs.value = []
    await loadJobs()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error(err.message)
  }
}

onMounted(() => {
  loadJobs()
})
</script>

<template>
  <div style="max-width: 1200px; margin: 0 auto; padding: 24px;">
    <!-- 标题 + 搜索 + 清空：合并成一个工具条卡片 -->
    <el-card class="app-card" shadow="never" style="margin-bottom: 16px;">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
        <h2 style="margin: 0; margin-right: auto;">任务历史</h2>
        <el-input
          v-model="searchQuery"
          placeholder="按标题或 URL 过滤"
          clearable
          @keyup.enter="doSearch"
          @clear="clearSearch"
          style="width: 260px;"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" :loading="searching" @click="doSearch">搜索</el-button>
        <el-button type="success" plain size="small" @click="handleClearByStatus('completed', '成功')">清空成功</el-button>
        <el-button type="danger" plain size="small" @click="handleClearFailed">清空失败/取消</el-button>
        <el-button type="danger" plain size="small" @click="handleClearAll">清空全部</el-button>
      </div>
    </el-card>

    <!-- 任务列表（搜索时按 keyword 过滤） -->
    <el-card class="app-card" shadow="never">
      <!-- 批量操作栏（选中任务时显示） -->
      <div v-if="selectedJobs.length > 0" class="batch-toolbar">
        <span class="batch-toolbar__count">已选 {{ selectedJobs.length }} 项</span>
        <el-button size="small" type="warning" @click="handleBatchCancel">
          批量取消（{{ selectedJobs.filter(j => activeStatuses.includes(j.status)).length }}）
        </el-button>
        <el-button size="small" type="danger" @click="handleBatchDelete">批量删除</el-button>
        <el-button size="small" type="success" @click="handleBatchExport">
          批量导出结果（{{ selectedJobs.filter(j => j.status === 'completed').length }}）
        </el-button>
        <el-button size="small" link @click="selectedJobs = []">取消选择</el-button>
      </div>

      <el-table :data="jobs" v-loading="loading" stripe style="width: 100%;" :empty-text="searchQuery.trim() ? '未找到匹配的任务' : '暂无任务'" @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="platform" label="平台" width="90" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="jobStatusTagType(row.status)" size="small" effect="plain" round>
              {{ jobStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="模型" width="140" show-overflow-tooltip />
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="340" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link :icon="Eye" @click="handlePrimaryAction(row)">
              {{ actionFor(row).primaryLabel }}
            </el-button>
            <el-button
              v-if="actionFor(row).secondary"
              size="small"
              type="warning"
              link
              @click="handleSecondaryAction(row)"
            >{{ actionFor(row).secondaryLabel }}</el-button>
            <el-button
              v-if="actionFor(row).canCancel"
              size="small" type="danger" link
              @click="handleCancel(row.id)"
            >取消</el-button>
            <el-button size="small" type="danger" link :icon="Trash2" @click="handleDelete(row.id)">删除</el-button>
            <el-button
              v-if="row.status === 'failed' || row.status === 'cancelled'"
              size="small"
              link
              @click="viewResult(row.id)"
            >查看详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-if="total > pageSize"
        style="margin-top: 16px; justify-content: center;"
        :current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        @current-change="handlePageChange"
      />
    </el-card>
  </div>
</template>

<style scoped>
/* 批量删除确认弹窗：消息可能很长，限制高度并滚动 */
:deep(.batch-delete-dialog .el-message-box__message) {
  max-height: 300px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
  line-height: 1.6;
}

/* ========== 批量操作工具栏 ========== */
.batch-toolbar {
  margin-bottom: 12px;
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 14px;
  border-radius: 6px;
}
.batch-toolbar__count {
  font-weight: 600;
}

:root[data-theme="light"] .batch-toolbar {
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-5);
}
:root[data-theme="light"] .batch-toolbar__count {
  color: var(--el-color-primary);
}

:root[data-theme="dark"] .batch-toolbar {
  background: rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.12);
  border: 1px solid rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.22);
}
:root[data-theme="dark"] .batch-toolbar__count {
  color: var(--app-accent-1, #38bdf8);
}
</style>
