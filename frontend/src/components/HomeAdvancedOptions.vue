<script setup>
/**
 * 工作台「高级选项」卡片（HomeView 拆分 slice-2，2026-09-12；v4 三区重构 2026-09-13）
 * - 自持：settings/modelStore（Pinia 全局）+ 引擎提示 computed
 * - 三区管线：① 任务目标（模式卡+输出素材随模式展开） ② 文本获取与转录 ③ AI 加工
 * - outputOptions 在 settingsStore（结果页下载素材过滤 + video→download_video 提交映射）
 * - 「AI 模型」手动选择时 emit('touch-model')，父组件据此停止按字数自动推荐
 * - 「转录引擎」手动选择时调 settingsStore.touchEngine()，之后不再被后端推荐值覆盖
 */
import { computed, watch } from 'vue'
import { useSettingsStore } from '../stores/settingsStore'
import { useModelStore } from '../stores/modelStore'
import { modelDisplayName } from '../utils/modelDisplay'

const settings = useSettingsStore()
const modelStore = useModelStore()

const emit = defineEmits(['touch-model'])

// ★ 仅生成字幕模式下字幕是任务本体：输出素材强制包含 srt（结果页不可把它过滤掉）
watch(() => settings.jobGoal, (goal) => {
  if (goal === 'subtitle_only' && !settings.outputOptions.includes('srt')) {
    settings.outputOptions = [...settings.outputOptions, 'srt']
  }
}, { immediate: true })

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
    return settings.freeCloudAvailable ? '' : '免费引擎未配置（config.py 缺 ASR_FREE_API_KEY）'
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
      <!-- ① 任务目标：模式卡 + 输出素材随模式展开（v4，2026-09-13）。
           仅生成字幕 = do_summary=false（后端本就「拿完文本即收尾」），此处只做语义包装；
           历史回填/提交 payload 直读 do_summary，无需新字段 -->
      <section class="adv-section">
        <div class="adv-section-title">任务目标</div>
        <div class="mode-grid">
          <label class="mode-card" :class="{ on: settings.jobGoal === 'summary' }">
            <input v-model="settings.jobGoal" type="radio" value="summary" class="mode-input" />
            <span class="mode-dot" aria-hidden="true"></span>
            <span class="adv-switch-meta">
              <span class="adv-switch-name">总结成文</span>
              <span class="adv-switch-desc">转录/字幕 → AI 总结 → 打标签，结果可追问与导出</span>
            </span>
          </label>
          <label class="mode-card" :class="{ on: settings.jobGoal === 'subtitle_only' }">
            <input v-model="settings.jobGoal" type="radio" value="subtitle_only" class="mode-input" />
            <span class="mode-dot" aria-hidden="true"></span>
            <span class="adv-switch-meta">
              <span class="adv-switch-name">仅生成字幕</span>
              <span class="adv-switch-desc">只要字幕文件，不做 AI 总结</span>
            </span>
          </label>
        </div>
        <!-- ★ 具体提示：得到什么/不做什么/抽查仍开，选中即见，不留猜测 -->
        <p v-if="settings.jobGoal === 'subtitle_only'" class="adv-mode-hint">
          完成后得到 SRT 字幕 + 转录文本，可在结果页下载；不做 AI 总结、不打标签、不消耗总结 token。
          B站字幕拿到后仍会做内容抽查（防旧字幕）；文本来源选「自动」时，平台字幕拿不到会自动转录。
        </p>
        <!-- ★ 输出素材随模式展开（用户原意）：勾选 = 结果页「下载素材」实际展示该产物；
             「视频」勾选在提交时映射为既有 options.download_video（链接任务真实下载视频原件） -->
        <div class="adv-output-block">
          <div class="adv-field-label">输出素材</div>
          <el-checkbox-group v-model="settings.outputOptions" class="adv-output-group">
            <el-checkbox
              v-if="settings.jobGoal === 'summary'"
              value="result_md"
              border
              class="adv-output-item"
            >AI 总结 (.md)</el-checkbox>
            <el-checkbox
              value="srt"
              border
              class="adv-output-item"
              :disabled="settings.jobGoal === 'subtitle_only'"
            >字幕 (.srt)</el-checkbox>
            <el-checkbox value="txt" border class="adv-output-item">转录文本 (.txt)</el-checkbox>
            <el-checkbox value="audio" border class="adv-output-item">纯音频 (.m4a)</el-checkbox>
            <el-checkbox value="video" border class="adv-output-item">视频 (.mp4)</el-checkbox>
          </el-checkbox-group>
          <p class="adv-output-hint">
            勾选 = 结果页「下载素材」中展示该产物；「视频」勾选时链接任务会下载视频原件（较大，仅在发生下载时生效）
          </p>
        </div>
      </section>

      <!-- ② 文本获取与转录：文本来源常显（吸收原「强制转录」tile——同一 force_transcribe 选项单一控制点） -->
      <section class="adv-section">
        <div class="adv-section-title">文本获取与转录</div>
        <div class="src-row src-row--lead">
          <span class="adv-field-label">文本来源</span>
          <label class="src-chip" :class="{ on: settings.subtitleSource === 'auto' }">
            <input v-model="settings.subtitleSource" type="radio" value="auto" class="mode-input" />
            <span class="mode-dot" aria-hidden="true"></span>自动：优先平台字幕，没有就转录
          </label>
          <label class="src-chip" :class="{ on: settings.subtitleSource === 'transcribe' }">
            <input v-model="settings.subtitleSource" type="radio" value="transcribe" class="mode-input" />
            <span class="mode-dot" aria-hidden="true"></span>直接转录生成
          </label>
          <span class="src-hint">仅视频链接生效，本地文件自动转录</span>
        </div>
        <div class="adv-field-grid">
          <div class="adv-field">
            <div class="adv-field-label">转录引擎</div>
            <el-select
              v-model="settings.advancedOptions.transcribe_engine"
              class="adv-field-control"
              @change="settings.touchEngine()"
            >
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
        <!-- 说话人分离：转录域配置归位（仅本地 Whisper 生效） -->
        <label
          class="adv-switch-tile adv-switch-tile--single"
          :class="{ on: settings.advancedOptions.speaker_diarization }"
        >
          <el-switch v-model="settings.advancedOptions.speaker_diarization" size="small" />
          <span class="adv-switch-meta">
            <span class="adv-switch-name">说话人分离</span>
            <span class="adv-switch-desc">{{ speakerDiarizationDesc }}</span>
          </span>
        </label>
      </section>

      <!-- ③ AI 加工：总结/校正共用模型 + 文本加工开关 -->
      <section class="adv-section adv-section--last">
        <div class="adv-section-title">AI 加工</div>
        <div class="adv-field adv-model-field">
          <div class="adv-field-label">AI 模型（总结 / 校正共用）</div>
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
        </div>
        <div class="adv-switch-grid">
          <!-- ★ AI 校正：对所有文本来源生效（B站/平台字幕、云端/本地转录、文本输入）；
               文案明示代价——校正=全文逐字重新生成，慢且费 token，避免用户误以为"秒级小修" -->
          <label class="adv-switch-tile" :class="{ on: settings.advancedOptions.ai_correct }">
            <el-switch v-model="settings.advancedOptions.ai_correct" size="small" />
            <span class="adv-switch-meta">
              <span class="adv-switch-name">AI 校正</span>
              <span class="adv-switch-desc">代价大：全文重新生成，耗时数分钟、token 消耗多；字幕/转录均适用</span>
            </span>
          </label>
          <!-- 转录预览仅在总结成文模式有意义（仅字幕模式后端本就跳过预览） -->
          <label v-if="settings.doSummary" class="adv-switch-tile" :class="{ on: settings.previewBeforeSummary }">
            <el-switch v-model="settings.previewBeforeSummary" size="small" />
            <span class="adv-switch-meta">
              <span class="adv-switch-name">转录预览</span>
            </span>
          </label>
          <!-- ★ 忽略缓存（2026-10-06）：相同内容默认复用上次转录/AI 缓存结果；
               缓存不全或有害时打开它强制全新生成，新结果仍回写缓存池修复旧条目 -->
          <label class="adv-switch-tile" :class="{ on: settings.advancedOptions.bypass_cache }">
            <el-switch v-model="settings.advancedOptions.bypass_cache" size="small" />
            <span class="adv-switch-meta">
              <span class="adv-switch-name">忽略缓存</span>
              <span class="adv-switch-desc">本次任务不复用上次的识别/总结缓存，全部重新生成（缓存不全或有害时用）</span>
            </span>
          </label>
        </div>
        <!-- ★ 校正子选项：仅开启 AI 校正时显示（跟在处理开关后） -->
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
.adv-model-field {
  margin-bottom: 12px;
}
.adv-model-select {
  width: 100%;
}

/* 流程开关：2 列宽卡网格（1040px 宽卡上 160px 会摊成 4-5 个窄条，描述挤成细长条） */
.adv-switch-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
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
.adv-switch-tile--single {
  margin-top: 12px;
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

/* 任务目标模式卡：与流程方块同视觉语言，置顶（2 列宽卡） */
.mode-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}
.mode-card {
  /* ★ 必须相对定位：隐藏 radio(.mode-input) 是 absolute，没有定位祖先时会
     飘到文档底部，label 点击聚焦触发 scrollIntoView，页面瞬间"飞"到底 */
  position: relative;
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
.mode-card:hover {
  border-color: rgba(var(--app-accent-1-rgb), 0.45);
}
.mode-card.on {
  border-color: rgba(var(--app-accent-1-rgb), 0.55);
  background: var(--app-chip-active-soft);
  box-shadow: 0 0 0 1px rgba(var(--app-accent-1-rgb), 0.12);
}
/* 原生 radio 视觉隐藏：label 点击切换，样式层 .mode-dot 表达选中态 */
.mode-input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}
.mode-dot {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  border-radius: 50%;
  border: 1.5px solid var(--app-text-secondary);
  box-sizing: border-box;
  position: relative;
}
.mode-card.on .mode-dot,
.src-chip.on .mode-dot {
  border-color: rgb(var(--app-accent-1-rgb));
}
.mode-card.on .mode-dot::after,
.src-chip.on .mode-dot::after {
  content: "";
  position: absolute;
  inset: 2.5px;
  border-radius: 50%;
  background: rgb(var(--app-accent-1-rgb));
}
/* 仅字幕模式提示块：得到什么/不做什么，选中即见 */
.adv-mode-hint {
  margin: 0 0 12px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px dashed var(--app-border);
  background: var(--app-hover-bg, transparent);
  font-size: 11.5px;
  color: var(--app-text-secondary);
  line-height: 1.55;
}
/* 输出素材块：随模式展开在任务目标区内 */
.adv-output-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.adv-output-hint {
  margin: 0;
  font-size: 11px;
  color: var(--app-text-secondary);
  line-height: 1.4;
}
/* 字幕来源子选项行：lead 变体作为区块首行不带虚线分隔 */
.src-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 10px;
  margin-bottom: 12px;
}
.src-row--lead {
  padding-bottom: 12px;
  border-bottom: 1px dashed var(--app-border);
}
.src-chip {
  position: relative; /* 同 .mode-card：锚住隐藏 radio，防点击后页面跳滚 */
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid var(--app-border);
  background: var(--app-hover-bg, transparent);
  cursor: pointer;
  font-size: 12px;
  color: var(--app-text);
  transition: border-color 0.15s, background 0.15s;
}
.src-chip .mode-dot {
  width: 12px;
  height: 12px;
  margin-top: 0;
}
.src-chip.on {
  border-color: rgba(var(--app-accent-1-rgb), 0.55);
  background: var(--app-chip-active-soft);
}
.src-hint {
  font-size: 11px;
  color: var(--app-text-secondary);
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
/* 校正子选项：跟在开关网格下方，留出一行呼吸空间 */
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
