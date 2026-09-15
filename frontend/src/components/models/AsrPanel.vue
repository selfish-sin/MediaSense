<script setup>
/**
 * ASR 模型域容器（模型管理 → ASR 模型）
 *
 * 一级 Tab 已把「LLM 模型」与「ASR 模型」分开，这里在 ASR 域内再小分「云端 / 本地」。
 * 分域语义：ASR 配置（provider / 本地模型 / 默认引擎）与 LLM 模型配置互不影响。
 */
import { ref } from 'vue'
import AsrCloudPanel from './AsrCloudPanel.vue'
import AsrLocalPanel from './AsrLocalPanel.vue'

const section = ref('cloud')
</script>

<template>
  <div>
    <div class="card-head-row" style="margin-bottom: 12px;">
      <span class="wb-card-title">ASR 语音模型</span>
      <span class="card-head-hint">
        语音转文字（转录）引擎，与 LLM 文本模型分开管理：配置、密钥、默认值互不影响
      </span>
    </div>

    <el-radio-group v-model="section" style="margin-bottom: 16px;">
      <el-radio-button value="cloud">云端 ASR</el-radio-button>
      <el-radio-button value="local">本地 ASR</el-radio-button>
    </el-radio-group>

    <AsrCloudPanel v-if="section === 'cloud'" />
    <AsrLocalPanel v-else />
  </div>
</template>
