import { ElMessage } from 'element-plus'
import { jobsApi } from '../utils/api'
import { ACTIVE_JOB_STATUSES } from '../utils/jobActions'

/**
 * rerun/restore 路由参数处理与任务配置回填。
 * 依赖注入视图状态（refs/stores/路由），避免与具体视图硬耦合。
 */
export function useJobRestore(deps) {
  const {
    inputType, inputUrl, inputText,
    uploadedFilePath, uploadedFileName,
    outputOptions, customPrompt, promptOverride, usePromptOverride,
    rerunSourceJobId,
    settings, jobStore, route, router,
    scrollToJobProgress,
  } = deps

  let _routeQueryHandled = false

  function applyJobConfig(job) {
    const options = job?.options && typeof job.options === 'object' ? job.options : {}
    const sourceType = options.input_type || (job.input_url ? 'url' : job.input_text ? 'text' : 'file')
    inputType.value = sourceType
    inputUrl.value = job.input_url || ''
    inputText.value = job.input_text || ''
    uploadedFilePath.value = job.input_file || ''
    uploadedFileName.value = uploadedFilePath.value.split(/[\\/]/).pop() || ''

    if (job.model) settings.selectedModel = job.model
    if (job.summary_type) settings.selectedSummaryType = job.summary_type

    const dimensions = options.dimensions && typeof options.dimensions === 'object'
      ? { ...settings.selectedDimensions, ...options.dimensions }
      : { ...settings.selectedDimensions }
    settings.selectedDimensions = dimensions

    const restoredOptions = { ...options, dimensions }
    delete restoredOptions._retry
    delete restoredOptions.input_type
    delete restoredOptions.do_summary
    delete restoredOptions.do_translate
    delete restoredOptions.do_subtitle
    delete restoredOptions.preview_before_summary
    delete restoredOptions.output_options
    settings.advancedOptions = { ...settings.advancedOptions, ...restoredOptions, dimensions }

    settings.doSummary = options.do_summary ?? true
    settings.doTranslate = options.do_translate ?? false
    settings.doSubtitle = options.do_subtitle ?? false
    settings.previewBeforeSummary = options.preview_before_summary ?? false
    outputOptions.value = Array.isArray(options.output_options)
      ? [...options.output_options]
      : ['result_md', 'srt', 'txt']

    customPrompt.value = options.custom_prompt || ''
    settings.fullOverride = options.full_override || ''
    settings.useFullOverride = Boolean(options.full_override)
    promptOverride.value = options.edited_prompt || ''
    usePromptOverride.value = Boolean(options.edited_prompt)
  }

  async function handleRouteQuery() {
    const rerunJobId = route.query.rerun && route.query.rerun !== '1'
      ? route.query.rerun
      : route.query.rerun === '1' ? route.query.restore : null
    let restoreJobId = null

    if (rerunJobId) {
      try {
        const sourceJob = await jobsApi.get(rerunJobId)
        jobStore.reset()
        applyJobConfig(sourceJob)
        rerunSourceJobId.value = rerunJobId
        ElMessage.info('已载入原任务配置，可修改后重新处理')
      } catch (err) {
        rerunSourceJobId.value = null
        ElMessage.error(`加载原任务配置失败: ${err.message}`)
      }
    } else if (route.query.restore) {
      restoreJobId = route.query.restore
    } else if (!_routeQueryHandled && jobStore.tryRestore()) {
      restoreJobId = jobStore.currentJobId
    }

    if (restoreJobId) {
      try {
        const loadedJob = await jobStore.loadJob(restoreJobId)
        applyJobConfig(loadedJob)
        rerunSourceJobId.value = null

        const freshStatus = jobStore.jobStatus
        if (ACTIVE_JOB_STATUSES.includes(freshStatus)) {
          jobStore.isProcessing = true
          jobStore.addLog(`已恢复任务 ${restoreJobId}（状态: ${freshStatus}）`)
          if (freshStatus === 'preview' && jobStore.previewText) {
            jobStore.addLog('转录预览待确认，请在下方检查并继续')
          }
          scrollToJobProgress()
        } else {
          jobStore.isProcessing = false
        }
      } catch (err) {
        jobStore.reset()
      }
    }

    // 恢复完成后再清除 query 参数（避免 router.replace 与加载并发导致状态错乱）
    if (route.query.restore || route.query.rerun) {
      await router.replace({ path: '/', query: {} }).catch((err) => {
        // 导航失败不阻断恢复流程，但必须留下痕迹（不静默吞错）
        console.warn('[useJobRestore] 清除路由参数失败:', err)
      })
    }

    _routeQueryHandled = true
  }

  return { applyJobConfig, handleRouteQuery }
}
