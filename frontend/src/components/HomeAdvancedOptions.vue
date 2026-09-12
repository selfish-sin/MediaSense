<script setup>
/**
 * 工作台「高级选项」卡片（HomeView 拆分 slice-2，2026-09-12）
 * - 自持：settings/modelStore（Pinia 全局）+ 引擎提示 computed
 * - outputOptions 用 v-model:output-options 与父组件双向同步（提交时父组件读取）
 * - 「AI 模型」手动选择时 emit('touch-model')，父组件据此停止按字数自动推荐
 */
import { computed } from 'vue'
import { useSettingsStore } from '../stores/settingsStore'
import { useModelStore } from '../stores/modelStore'
import { modelDisplayName } from '../utils/modelDisplay'

const settings = useSettingsStore()
const modelStore = useModelStore()

const outputOptions = defineModel('outputOptions', { type: Array, required: true })
const emit = defineEmits(['touch-model'])

// ── 校正模型选择器占位：展示后端配置的校正默认模型（留空时生效的那个） ──
const correctModelPlaceholder = computed(() => {
  const d = modelStore.correctDefault
  if (!d) return '留空则用主模型'
  const m = modelStore.models.find((x) => x.key === d)
  return m ? `留空用默认：${modelDisplayName(m)}` : `留空用默认：${d}`
})

// ── 转录引擎相关的提示文案（cloud/cloud_free/firered/local） ──
// 引擎异常状态提示（未配置/需下载时显示，正常为空不渲染）
const engineHint = computed(() => {
  const engine = settings.advancedOptions.transcribe_engine
  if (engine === 'firered') {
    return settings.fireredReady ? '' : '首次使用将自动下载约 1.1GB 模型'
  }
  if (engine === 'local') return ''
  if (engine === 'cloud_free') {
    return settings.freeCloudAvailable ? '' : '免费档未配置（config.py 缺 ASR_FREE_API_KEY）'
  }
  return settings.cloudAsrAvailable ? '' : '云端未配置（config.py 缺 ASR_CLOUD_API_KEY）'
})
const speakerDiarizationDesc = computed(() => {
  const engine = settings.advancedOptions.transcribe_engine
  if (engine === 'cloud' || engine === 'cloud_free') return '云端引擎暂不支持，仅本地 Whisper 生效'
  if (engine === 'firered') return 'FireRedASR 暂不支持，仅本地 Whisper 生效'
  return '区分不同发言人'
})
const transcribeLanguageHint = computed(() => {
  const engine = settings.advancedOptions.transcribe_engine
  if (engine === 'cloud' || engine === 'cloud_free') return '云端自动识别语种，此设置仅本地转录生效'
  if (engine === 'firered') return '支持中英，自动识别'
  return '外文视频请改对应语言'
})
</script>

<template>
  <el-card class="wb-card advanced-card" shadow="never">
    <template #header>
      <div class="card-head-row">
        <span class="wb-card-title">高级选项</span>
      </div>
    </template>

    <div class="adv-body">
      <!-- ① AI 模型 -->
      <section class="adv-section">
        <div class="adv-section-title">AI 模型</div>
        <el-select
          v-model="settings.selectedModel"
          class="adv-model-select"
          placeholder="选择模型"
          filterable
          @change="emit('touch-model')"
        >
          <el-option
            v-for="m in modelStore.models"
            :key="m.key"
            :label="modelDisplayName(m)"
            :value="m.key"
          />
        </el-select>
      </section>

      <!-- ② 处理流程开关 -->
      <section class="adv-section">
        <div class="adv-section-title">处理流程</div>
        <div class="adv-switch-grid">
          <label class="adv-switch-tile" :class="{ on: settings.doSummary }">
            <el-switch v-model="settings.doSummary" size="small" />
            <span class="adv-switch-meta">
              <span class="adv-switch-name">AI 总结</span>
            </span>
          </label>
          <label class="adv-switch-tile" :class="{ on: settings.previewBeforeSummary }">
            <el-switch v-model="settings.previewBeforeSummary" size="small" />
            <span class="adv-switch-meta">
              <span class="adv-switch-name">转录预览</span>
            </span>
          </label>
          <label class="adv-switch-tile" :class="{ on: settings.advancedOptions.speaker_diarization }">
            <el-switch v-model="settings.advancedOptions.speaker_diarization" size="small" />
            <span class="adv-switch-meta">
              <span class="adv-switch-name">说话人分离</span>
              <span class="adv-switch-desc">{{ speakerDiarizationDesc }}</span>
            </span>
          </label>
          <!-- ★ 强制转录：决定"要不要用视频自带字幕"，与具体转录引擎无关（原「强制 Whisper 转录」易误解） -->
          <label class="adv-switch-tile" :class="{ on: settings.advancedOptions.force_transcribe }">
            <el-switch v-model="settings.advancedOptions.force_transcribe" size="small" />
            <span class="adv-switch-meta">
              <span class="adv-switch-name">强制转录</span>
              <span class="adv-switch-desc">跳过视频字幕，直接转录音频</span>
            </span>
          </label>
          <!-- ★ AI 校正：对所有文本来源生效（B站/平台字幕、云端/本地转录、文本输入）；
               文案明示代价——校正=全文逐字重新生成，慢且费 token，避免用户误以为"秒级小修" -->
          <label class="adv-switch-tile" :class="{ on: settings.advancedOptions.ai_correct }">
            <el-switch v-model="settings.advancedOptions.ai_correct" size="small" />
            <span class="adv-switch-meta">
              <span class="adv-switch-name">AI 校正</span>
              <span class="adv-switch-desc">代价大：全文重新生成，耗时数分钟、token 消耗多；字幕/转录均适用</span>
            </span>
          </label>
        </div>
        <!-- ★ 校正子选项：仅开启 AI 校正时显示（跟在处理流程开关后，不再塞进语音转录栏） -->
        <div v-if="settings.advancedOptions.ai_correct" class="adv-field-grid adv-correct-grid">
          <div class="adv-field">
            <div class="adv-field-label">校正领域</div>
            <el-select
              v-model="settings.advancedOptions.ai_correct_domain"
              class="adv-field-control"
            >
              <el-option label="通用" value="default" />
              <el-option label="中医 / 针灸" value="tcm" />
            </el-select>
          </div>
          <div class="adv-field">
            <div class="adv-field-label">校正模型</div>
            <el-select
              v-model="settings.advancedOptions.ai_correct_model"
              class="adv-field-control"
              :placeholder="correctModelPlaceholder"
              clearable
              filterable
            >
              <el-option
                v-for="m in modelStore.models"
                :key="m.key"
                :label="modelDisplayName(m)"
                :value="m.key"
              />
            </el-select>
          </div>
        </div>
      </section>

      <!-- ③ 语音转录 -->
      <section class="adv-section">
        <div class="adv-section-title">语音转录</div>
        <div class="adv-field-grid">
          <div class="adv-field">
            <div class="adv-field-label">转录引擎</div>
            <el-select v-model="settings.advancedOptions.transcribe_engine" class="adv-field-control">
              <el-option label="云端 MiMo-ASR" value="cloud" :disabled="!settings.cloudAsrAvailable" />
              <el-option label="云端 XingChen（免费）" value="cloud_free" :disabled="!settings.freeCloudAvailable" />
              <el-option label="本地 FireRedASR" value="firered" />
              <el-option label="本地 Whisper" value="local" />
            </el-select>
            <p v-if="engineHint" class="adv-field-hint">{{ engineHint }}</p>
          </div>
          <template v-if="settings.advancedOptions.transcribe_engine === 'local'">
            <div class="adv-field">
              <div class="adv-field-label">Whisper 模型</div>
              <el-select v-model="settings.advancedOptions.model_size" class="adv-field-control">
                <el-option label="tiny · 最快 / 精度低" value="tiny" />
                <el-option label="base · 快速 / 中文尚可" value="base" />
                <el-option label="small · 平衡" value="small" />
                <el-option label="medium · 高质量" value="medium" />
              </el-select>
            </div>
            <div class="adv-field">
              <div class="adv-field-label">转录设备</div>
              <el-select v-model="settings.advancedOptions.whisper_device" class="adv-field-control">
                <el-option label="自动" value="auto" />
                <el-option label="CUDA GPU" value="cuda" />
                <el-option label="CPU" value="cpu" />
              </el-select>
            </div>
          </template>
          <div class="adv-field">
            <div class="adv-field-label">转录语言</div>
            <el-select v-model="settings.advancedOptions.transcribe_language" class="adv-field-control">
              <el-option label="中文" value="zh" />
              <el-option label="英语" value="en" />
              <el-option label="日语" value="ja" />
              <el-option label="韩语" value="ko" />
              <el-option label="法语" value="fr" />
              <el-option label="德语" value="de" />
              <el-option label="自动检测" :value="null" />
            </el-select>
            <p v-if="transcribeLanguageHint" class="adv-field-hint">{{ transcribeLanguageHint }}</p>
          </div>
        </div>
      </section>

      <!-- ④ 输出素材 -->
      <section class="adv-section adv-section--last">
        <div class="adv-section-title">输出素材</div>
        <el-checkbox-group v-model="outputOptions" class="adv-output-group">
          <el-checkbox value="result_md" border class="adv-output-item">AI 总结 (.md)</el-checkbox>
          <el-checkbox value="srt" border class="adv-output-item">字幕 (.srt)</el-checkbox>
          <el-checkbox value="txt" border class="adv-output-item">转录文本 (.txt)</el-checkbox>
          <el-checkbox value="audio" border class="adv-output-item">纯音频 (.m4a)</el-checkbox>
          <el-checkbox value="video" border class="adv-output-item">视频 (.mp4)</el-checkbox>
        </el-checkbox-group>
      </section>
    </div>
  </el-card>
</template>

<style scoped>
.advanced-card :deep(.el-card__body) {
  padding-top: 14px;
}
.adv-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.adv-section {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--app-border);
}
.adv-section--last {
  padding-bottom: 0;
  border-bottom: none;
}
.adv-section-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--app-text-secondary);
  margin-bottom: 10px;
}
.adv-model-select {
  width: 100%;
}

/* 流程开关：等宽卡片网格 */
.adv-switch-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 8px;
}
.adv-switch-tile {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 0;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--app-border);
  background: var(--app-hover-bg, transparent);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.adv-switch-tile:hover {
  border-color: rgba(var(--app-accent-1-rgb), 0.45);
}
.adv-switch-tile.on {
  border-color: rgba(var(--app-accent-1-rgb), 0.55);
  background: var(--app-chip-active-soft);
  box-shadow: 0 0 0 1px rgba(var(--app-accent-1-rgb), 0.12);
}
.adv-switch-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.adv-switch-name {
  font-size: 13px;
  font-weight: 650;
  color: var(--app-text-heading);
  line-height: 1.3;
}
.adv-switch-desc {
  font-size: 11px;
  color: var(--app-text-secondary);
  line-height: 1.35;
}

/* 转录字段网格 */
.adv-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 14px;
}
@media (max-width: 720px) {
  .adv-field-grid {
    grid-template-columns: 1fr;
  }
}
.adv-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.adv-field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-text);
}
.adv-field-control {
  width: 100%;
}
.adv-field-hint {
  margin: 0;
  font-size: 11px;
  color: var(--app-text-secondary);
  line-height: 1.4;
}
/* 校正子选项：跟在处理流程开关网格下方，留出一行呼吸空间 */
.adv-correct-grid {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--app-border);
}

/* 输出素材 */
.adv-output-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.adv-output-item {
  margin: 0 !important;
  border-radius: 8px !important;
  height: auto !important;
  padding: 8px 12px !important;
}
.adv-output-item :deep(.el-checkbox__label) {
  font-size: 12px;
  font-weight: 550;
  color: var(--app-text);
  padding-left: 6px;
}
</style>
