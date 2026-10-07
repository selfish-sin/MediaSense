<script setup>
/**
 * 转录来源说明（结果可解释性，2026-10-07）
 *
 * 回答"这份结果怎么来的"：字幕还是识别的、实际引擎是谁、是否复用缓存、是否降级。
 * 用户据此判断要不要换引擎重跑。首页结果面板与结果详情页共用本组件，保证形态一致。
 * meta 为 null（历史任务/未产生转录）时不渲染，不留空壳。
 */
import { computed } from 'vue'
import { FileText } from 'lucide-vue-next'

const props = defineProps({
  // JobResponse.transcript_meta
  meta: { type: Object, default: null },
})

const SOURCE_LABELS = {
  platform_subtitle: '平台字幕',
  subtitle_file: '上传的字幕文件',
  text_input: '直接文本输入（未经识别）',
  text_file: '上传的文本文件（未经识别）',
  transcription: '语音识别',
}

const PLATFORM_LABELS = {
  bilibili: 'B站',
  douyin: '抖音',
  youtube: 'YouTube',
}

const CHECK_LABELS = {
  passed: '内容抽查通过',
  skipped: '内容抽查已关闭，未比对',
  unavailable: '内容抽查不可用，按原结果采用',
}

const CACHE_LABELS = {
  hit: '复用了同音频的转录缓存，未重新识别',
  bypassed: '已忽略缓存，本次全新识别',
  miss: '本次为全新识别',
}

const visible = computed(() => !!props.meta && !!SOURCE_LABELS[props.meta.source])

const sourceText = computed(() => {
  const platform = PLATFORM_LABELS[props.meta?.subtitle_from]
  const check = CHECK_LABELS[props.meta?.subtitle_check]
  let text = platform ? `${platform}平台字幕` : SOURCE_LABELS[props.meta?.source] || ''
  if (check) text += `（${check}）`
  return text
})

const engineText = computed(() => {
  const label = props.meta?.engine_label || props.meta?.engine || ''
  if (!label) return ''
  return props.meta?.engine === 'local' && props.meta?.model_size
    ? `${label} · ${props.meta.model_size} 档`
    : label
})

const cacheText = computed(() => {
  if (props.meta?.resume) return '复用了上次已完成的转录文件（断点续传）'
  return CACHE_LABELS[props.meta?.cache] || ''
})

const degraded = computed(() => props.meta?.degraded === true)
</script>

<template>
  <div v-if="visible" class="prov">
    <div class="prov-head">
      <el-icon class="prov-icon"><FileText /></el-icon>
      <span>这份结果的来源</span>
    </div>
    <div class="prov-line">
      <span class="prov-key">来源</span>
      <span class="prov-val">{{ sourceText }}</span>
    </div>
    <div v-if="engineText" class="prov-line">
      <span class="prov-key">识别引擎</span>
      <span class="prov-val">{{ engineText }}</span>
    </div>
    <div v-if="cacheText" class="prov-line">
      <span class="prov-key">缓存</span>
      <span class="prov-val">{{ cacheText }}</span>
    </div>
    <div v-if="degraded" class="prov-line prov-line--warn">
      <span class="prov-key">降级</span>
      <span class="prov-val">
        首选「{{ meta.requested_engine_label || meta.requested_engine }}」不可用，实际由「{{ engineText }}」完成；
        环境恢复后重跑可用回首选引擎
      </span>
    </div>
  </div>
</template>

<style scoped>
.prov {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: 4px;
  background: var(--app-hover-bg);
}

.prov-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}

.prov-icon {
  color: var(--app-accent);
}

.prov-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12.5px;
  line-height: 1.7;
}

.prov-key {
  flex: none;
  width: 58px;
  color: var(--app-text-secondary);
}

.prov-val {
  color: var(--app-text);
  word-break: break-all;
}

.prov-line--warn .prov-val {
  color: var(--el-color-warning);
}
</style>
