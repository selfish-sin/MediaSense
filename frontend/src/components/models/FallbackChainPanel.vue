<script setup>
/**
 * AI 调用兜底环配置面板（2026-10-05）
 *
 * 定位：把「模型失败后换下一个重试」从隐式约定变成用户可见、可排序的配置。
 * 此前 main.py 的候选是隐式的（其余所有有密钥的模型按创建顺序），既调不了序也关不掉。
 *
 * 形态是**环**而不是带主档的链：
 * - 没有"主档"概念——每次任务从**本次所选模型**起步，环里其余按优先级跟在后面；
 * - **不设条数上限**——环长由用户自己负责，靠每次切换都推日志让人看得见；
 * - 每个模型只试一次、不回头（理由见后端 resolve_ai_candidates 的说明）。
 *
 * 视觉：沿用 /models 页既有语言（app-card + card-head-row + wb-card-title + tag），
 * 环体用「左侧导轨 + 序号徽章」，末尾一个回环标记收口。
 * 只消费既有 --app-* token，不新增（frontend spec 冻结 token 语义）。
 */
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowUp, ArrowDown, X, Plus, CircleCheck, CircleX } from 'lucide-vue-next'
import { useModelStore } from '../../stores/modelStore'
import { modelDisplayName } from '../../utils/modelDisplay'

const modelStore = useModelStore()

const pickKey = ref('')
const saving = ref(false)
// 每个候选的连接自测结果：key -> {loading, state: 'ok'|'err', msg}
const probe = ref({})

const chain = computed(() => modelStore.fallbackChain)
const defaultModelName = computed(
  () => modelDisplayName(modelByKey(modelStore.defaultModel)) || '（未设置）'
)

/**
 * 可选候选：有可用密钥、不是当前默认模型、尚未在环中。
 * 密钥失效（key_broken，含脱敏星号）的模型不进环——放进去等于白白浪费一次重试。
 * 排除默认模型是后端同一规则的前端镜像（它已是首选档，重复占一格无意义）。
 */
const candidates = computed(() => {
  const used = new Set(chain.value)
  return modelStore.models.filter(
    (m) => m.key !== modelStore.defaultModel && !used.has(m.key) && !m.key_broken
  )
})

function modelByKey(key) {
  return modelStore.models.find((m) => m.key === key) || null
}

onMounted(() => {
  if (!modelStore.models.length) modelStore.loadModels()
  modelStore.loadFallbackChain()
})

// 不在此处做「模型已删→本地剔除」：后端 get_fallback_chain 读时已按现存模型过滤，
// 前端再判一次会变成对同一份真值的重复写入，反而制造竞态。

async function save(nextChain, enabled, silent = false) {
  saving.value = true
  try {
    await modelStore.saveFallbackChain(nextChain, enabled)
    if (!silent) ElMessage.success('兜底链已保存')
  } catch (err) {
    ElMessage.error(err?.message || '保存兜底链失败')
  } finally {
    saving.value = false
  }
}

function addCandidate() {
  const key = pickKey.value
  if (!key) {
    ElMessage.warning('请先选择一个模型')
    return
  }
  pickKey.value = ''
  save([...chain.value, key], modelStore.fallbackEnabled)
}

async function removeAt(idx) {
  const key = chain.value[idx]
  const m = modelByKey(key)
  try {
    await ElMessageBox.confirm(
      `将「${modelDisplayName(m)}」移出兜底环？该模型失败后将不再被用来重试。`,
      '移出兜底环',
      { type: 'warning' }
    )
  } catch {
    return
  }
  const next = chain.value.filter((_, i) => i !== idx)
  const nextProbe = { ...probe.value }
  delete nextProbe[key]
  probe.value = nextProbe
  save(next, modelStore.fallbackEnabled)
}

function move(idx, delta) {
  const to = idx + delta
  if (to < 0 || to >= chain.value.length) return
  const next = [...chain.value]
  const [item] = next.splice(idx, 1)
  next.splice(to, 0, item)
  save(next, modelStore.fallbackEnabled)
}

function toggleEnabled(v) {
  if (v && !chain.value.length) {
    ElMessage.warning('兜底链为空，请先添加至少一个候选模型')
    return
  }
  save(chain.value, v)
}

/** 连接自测：让用户按真实可用性排序，而不是凭印象 */
async function probeModel(key) {
  probe.value = { ...probe.value, [key]: { loading: true } }
  try {
    const res = await modelStore.testModel(key)
    const ok = !!res?.success
    probe.value = {
      ...probe.value,
      [key]: { state: ok ? 'ok' : 'err', msg: ok ? '连通' : (res?.error || '不可用') },
    }
  } catch (err) {
    probe.value = { ...probe.value, [key]: { state: 'err', msg: err?.message || '检测失败' } }
  }
}
</script>

<template>
  <el-card class="app-card" shadow="never" style="margin-top: 12px;" v-loading="modelStore.fallbackLoading">
    <div class="card-head-row">
      <div style="display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;">
        <span class="wb-card-title">调用兜底环</span>
        <span class="card-head-hint">任一 AI 调用失败时，从本次所选模型开始按下面顺序换模型重试</span>
      </div>
      <el-switch
        :model-value="modelStore.fallbackEnabled"
        :disabled="!chain.length"
        active-text="启用"
        @update:model-value="toggleEnabled"
      />
    </div>

    <div class="ring">
      <!-- 环内档位：可增删、可排序。第 1 档是"本次所选模型失败后第一个顶上来的" -->
      <div v-for="(key, idx) in chain" :key="key" class="step">
        <span class="step-badge">{{ idx + 1 }}</span>
        <div class="step-body">
          <span class="step-name">{{ modelDisplayName(modelByKey(key)) }}</span>
          <el-tag v-if="modelByKey(key)?.engine" size="small" effect="plain">{{ modelByKey(key).engine }}</el-tag>
          <span
            v-if="probe[key]?.state"
            class="step-probe"
            :class="probe[key].state === 'ok' ? 'step-probe--ok' : 'step-probe--err'"
            :title="probe[key].msg"
          >
            <el-icon v-if="probe[key].state === 'ok'"><CircleCheck /></el-icon>
            <el-icon v-else><CircleX /></el-icon>
            {{ probe[key].state === 'ok' ? '连通' : '不可用' }}
          </span>
        </div>
        <div class="step-actions">
          <el-tooltip content="测试连接" placement="top">
            <el-button
              size="small" link
              :loading="probe[key]?.loading"
              @click="probeModel(key)"
            >测试</el-button>
          </el-tooltip>
          <el-tooltip content="上移（更优先）" placement="top">
            <el-button size="small" link :disabled="idx === 0" @click="move(idx, -1)">
              <el-icon><ArrowUp /></el-icon>
            </el-button>
          </el-tooltip>
          <el-tooltip content="下移" placement="top">
            <el-button size="small" link :disabled="idx === chain.length - 1" @click="move(idx, 1)">
              <el-icon><ArrowDown /></el-icon>
            </el-button>
          </el-tooltip>
          <el-tooltip content="移出兜底环" placement="top">
            <el-button size="small" link type="danger" @click="removeAt(idx)">
              <el-icon><X /></el-icon>
            </el-button>
          </el-tooltip>
        </div>
      </div>

      <!-- 空态：不是"暂无结果"式留白，而是说清不配会怎样 -->
      <div v-if="!chain.length" class="ring-empty">
        <span class="ring-empty-title">未配置兜底候选</span>
        <span class="ring-empty-sub">任一 AI 调用失败时，任务会直接判失败</span>
      </div>

      <!-- 添加位 -->
      <div class="step step--add" :class="{ 'step--disabled': !candidates.length }">
        <span class="step-badge step-badge--add"><el-icon><Plus /></el-icon></span>
        <div class="step-body step-body--add">
          <el-select
            v-model="pickKey"
            filterable
            clearable
            :disabled="!candidates.length"
            :placeholder="candidates.length ? '选择要加入兜底环的模型' : '没有更多可用模型'"
            style="width: 100%;"
          >
            <el-option v-for="m in candidates" :key="m.key" :label="modelDisplayName(m)" :value="m.key" />
          </el-select>
          <el-button size="small" type="primary" plain :disabled="!pickKey" @click="addCandidate">
            加入环尾
          </el-button>
        </div>
      </div>

      <!-- 回环收口：环走完即止，不回头重试已失效的模型 -->
      <div v-if="chain.length" class="ring-close">
        <span class="ring-close-mark">↻</span>
        <span class="ring-close-text">
          环走完即止（每个模型只试一次）——本次任务从所选模型起步，失败才依次往后轮
        </span>
      </div>
    </div>

    <div class="ring-foot">
      <span v-if="!chain.length" class="ring-foot-warn">
        未配置兜底候选：AI 调用失败时任务将直接判失败。
      </span>
      <span v-else>
        已启用 {{ chain.length }} 个候选，作用于总结 / AI 校正 / 打标签 / 思维导图。
        可先用「测试」确认连通，再按优先级排序。
      </span>
      <span class="ring-foot-note">
        当前默认模型「{{ defaultModelName }}」不在环内（它是首选档）——改成默认时会自动从环里摘除。
        总结若已输出部分内容后中断，不会换模型（避免两个模型的文字混在一份文档里）。
      </span>
    </div>
  </el-card>
</template>

<style scoped>
/* ===== 环体：左侧导轨 + 序号徽章，表达「按序轮转」而非平铺集合 ===== */
.ring {
  --rail-x: 13px;             /* 导轨横坐标 = 徽章中心 */
  position: relative;
  margin-top: 4px;
  padding-left: 4px;
}

/* 导轨本体：贯穿整条环的竖线，画在各 step 的徽章之下 */
.ring::before {
  content: '';
  position: absolute;
  left: calc(var(--rail-x) - 1px);
  top: 18px;
  bottom: 18px;
  width: 2px;
  background: var(--app-border);
  border-radius: 1px;
}

.step {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 6px 10px 6px 0;
  border-radius: 8px;
  transition: background 0.15s;
}

.step:hover {
  background: var(--app-hover-bg);
}

.step-badge {
  position: relative;
  z-index: 1;                 /* 徽章压在导轨之上，导轨从其下穿过 */
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  /* 实底 + 同色外扩描边：导轨从徽章下方穿过时视觉上被"镂空"，不出现半透明叠线 */
  background: var(--app-card-bg);
  border: 1px solid var(--app-border);
  color: var(--app-text-secondary);
  font-size: 12px;
  line-height: 1;
  box-shadow: 0 0 0 4px var(--app-card-bg);
}

/* 主模型：主色实心环，一眼区分第 0 档（它是既定设置，不是本面板可改项） */
.step-badge--primary {
  background: var(--app-chip-active-soft);
  border-color: var(--app-accent-1);
  color: var(--app-accent-1);
  font-weight: 600;
}

.step-badge--add {
  border-style: dashed;
  color: var(--app-text-placeholder);
}

/* 空态：说清"不配会怎样"，而不是留一片空白 */
.ring-empty {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 0 10px 36px;
}

.ring-empty-title {
  font-size: 14px;
  color: var(--app-text-secondary);
}

.ring-empty-sub {
  font-size: 12px;
  color: var(--app-text-placeholder);
}

/* 回环收口：环走完即止的语义说明，同时收住导轨的视觉终点 */
.ring-close {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 0 0 36px;
  padding-top: 8px;
  border-top: 1px dashed var(--app-border);
  font-size: 12px;
  color: var(--app-text-placeholder);
  line-height: 1.6;
}

.ring-close-mark {
  flex: 0 0 auto;
  color: var(--app-accent-1);
  font-size: 13px;
}

.step-body {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.step-body--add {
  gap: 8px;
  max-width: 560px;
}

.step-name {
  color: var(--app-text);
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.step--primary .step-name {
  font-weight: 600;
}

.step-lock {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--app-text-placeholder);
}

.step-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}

.step:hover .step-actions,
.step-actions:focus-within {
  opacity: 1;                 /* 行内操作平时收敛，悬停/聚焦才显形，避免列表过花 */
}

/* 连接自测结果：复用引擎状态 token（ok/err 三件套），不新造配色 */
.step-probe {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
}

.step-probe--ok {
  background: var(--app-engine-ok-bg);
  color: var(--app-engine-ok-text);
  border: 1px solid var(--app-engine-ok-border);
}

.step-probe--err {
  background: var(--app-engine-err-bg);
  color: var(--app-engine-err-text);
  border: 1px solid var(--app-engine-err-border);
}

.ring-foot {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--app-border);
  font-size: 12px;
  color: var(--app-text-secondary);
  line-height: 1.7;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ring-foot-warn {
  color: var(--app-engine-err-text);
}

.ring-foot-note {
  color: var(--app-text-placeholder);
}
</style>
