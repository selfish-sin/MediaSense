<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import QRCode from 'qrcode'
import { bilibiliApi } from '../utils/api'

defineProps({
  collapsed: { type: Boolean, default: false },
})

const loggedIn = ref(false)
const sessdataExpired = ref(false)
const showDialog = ref(false)
const qrUrl = ref('')
const qrKey = ref('')
const qrStatus = ref('')
const loading = ref(false)
let pollTimer = null

async function checkStatus() {
  try {
    const res = await bilibiliApi.status()
    loggedIn.value = !!res.logged_in
    sessdataExpired.value = !!(res.logged_in && res.sessdata_expired)
  } catch { /* ignore */ }
}

async function openQR() {
  // 先检查状态
  await checkStatus()
  if (loggedIn.value) {
    // 已登录，显示已登录状态弹窗
    showDialog.value = true
    qrStatus.value = 'success'
    return
  }
  showDialog.value = true
  qrStatus.value = 'waiting'
  loading.value = true
  try {
    const res = await bilibiliApi.qr()
    if (res.qrcode_key) {
      qrKey.value = res.qrcode_key
      qrUrl.value = await QRCode.toDataURL(res.url, { width: 200, margin: 2 })
      startPolling()
    } else {
      qrStatus.value = 'error'
    }
  } catch {
    qrStatus.value = 'error'
  }
  loading.value = false
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const res = await bilibiliApi.qrCheck(qrKey.value)
      if (res.code === 0) {
        qrStatus.value = 'success'
        loggedIn.value = true
        stopPolling()
        setTimeout(() => { showDialog.value = false }, 1500)
      } else if (res.code === 86090) {
        qrStatus.value = 'scanned'
      } else if (res.code === 86038) {
        qrStatus.value = 'expired'
        stopPolling()
      }
    } catch { /* ignore */ }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function doLogout() {
  try { await bilibiliApi.logout(); loggedIn.value = false } catch { /* ignore */ }
}

function closeDialog() { showDialog.value = false; stopPolling() }
function regenerate() { qrStatus.value = 'waiting'; openQR() }

watch(showDialog, (val) => { if (!val) stopPolling() })
onMounted(() => { checkStatus() })
onBeforeUnmount(() => { stopPolling() })
</script>

<template>
  <button type="button" class="sidebar-action-btn bilibili-entry" @click="openQR" :title="loggedIn ? 'B站已登录（点击登出）' : 'B站扫码登录'">
    <el-icon style="font-size: 16px;">
      <svg viewBox="0 0 24 24" fill="currentColor">
        <path d="M17.813 4.653h.854c1.51.054 2.769.578 3.773 1.574 1.004.995 1.524 2.249 1.56 3.76v7.36c-.036 1.51-.556 2.769-1.56 3.773s-2.262 1.524-3.773 1.56H5.333c-1.51-.036-2.769-.556-3.773-1.56S.036 18.858 0 17.347v-7.36c.036-1.511.556-2.765 1.56-3.76 1.004-.996 2.262-1.52 3.773-1.574h.774l-1.174-1.12a1.234 1.234 0 01-.373-.906c0-.356.124-.658.373-.907l.027-.027c.267-.249.573-.373.92-.373.347 0 .653.124.92.373L9.653 4.44c.071.071.134.142.187.213h4.267a.836.836 0 01.16-.213l2.853-2.747c.267-.249.573-.373.92-.373.347 0 .662.151.929.4.267.249.391.551.391.907 0 .355-.124.657-.373.906zM5.333 7.24c-.746.018-1.373.276-1.88.773-.506.498-.769 1.13-.786 1.894v7.52c.017.764.28 1.395.786 1.893.507.498 1.134.756 1.88.773h13.334c.746-.017 1.373-.275 1.88-.773.506-.498.769-1.129.786-1.893v-7.52c-.017-.765-.28-1.396-.786-1.894-.507-.497-1.134-.755-1.88-.773zM8 11.107c.373 0 .684.124.933.373.25.249.383.569.4.96v1.173c-.017.391-.15.711-.4.96-.249.25-.56.374-.933.374s-.684-.125-.933-.374c-.25-.249-.383-.569-.4-.96V12.44c0-.373.129-.689.386-.947.258-.257.574-.386.947-.386zm8 0c.373 0 .684.124.933.373.25.249.383.569.4.96v1.173c-.017.391-.15.711-.4.96-.249.25-.56.374-.933.374s-.684-.125-.933-.374c-.25-.249-.383-.569-.4-.96V12.44c.017-.391.15-.711.4-.96.249-.249.56-.373.933-.373z"/>
      </svg>
    </el-icon>
    <span v-if="!collapsed" class="sidebar-action-label">B站</span>
  </button>

  <el-dialog v-model="showDialog" title="B站扫码登录" width="320px" :close-on-click-modal="false" @close="closeDialog">
    <div class="qr-container">
      <div v-if="loading" class="qr-loading">
        <el-icon class="is-loading" :size="48"><svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1010 10h-2a8 8 0 11-8-8V2z" fill="currentColor"/></svg></el-icon>
        <p>生成二维码中...</p>
      </div>
      <template v-else-if="qrStatus === 'waiting'">
        <img :src="qrUrl" alt="B站登录二维码" class="qr-image" />
        <p class="qr-hint">请使用B站APP扫码登录</p>
      </template>
      <template v-else-if="qrStatus === 'scanned'">
        <img :src="qrUrl" alt="B站登录二维码" class="qr-image scanned" />
        <p class="qr-hint" style="color: #409EFF;">已扫码，请在手机上确认</p>
      </template>
      <template v-else-if="qrStatus === 'success'">
        <div class="qr-success">
          <el-icon :size="48" :color="sessdataExpired ? '#E6A23C' : '#67C23A'"><svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z" fill="currentColor"/></svg></el-icon>
          <p>B站已登录</p>
          <p v-if="sessdataExpired" style="font-size: 12px; color: #E6A23C;">登录态已过期，AI字幕可能不可用，建议重新扫码</p>
          <p v-else style="font-size: 12px; color: #909399;">登录状态已持久化，无需重复扫码</p>
        </div>
      </template>
      <template v-else-if="qrStatus === 'expired'">
        <p class="qr-hint" style="color: #F56C6C;">二维码已过期</p>
        <el-button type="primary" size="small" @click="regenerate">重新生成</el-button>
      </template>
      <template v-else-if="qrStatus === 'error'">
        <p class="qr-hint" style="color: #F56C6C;">获取二维码失败</p>
        <el-button type="primary" size="small" @click="regenerate">重试</el-button>
      </template>
    </div>
    <template #footer v-if="loggedIn">
      <el-button size="small" type="danger" text @click="doLogout">登出</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
/* B站入口复用父级 .sidebar-action-btn 的尺寸/对齐，只覆盖颜色 */
.bilibili-entry:hover {
  color: #FB7299;
  background: rgba(251, 114, 153, 0.12);
}

.qr-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 0;
  min-height: 200px;
}
.qr-image {
  width: 200px;
  height: 200px;
  border-radius: 8px;
  border: 2px solid #e4e7ed;
}
.qr-image.scanned { opacity: 0.5; }
.qr-hint { margin-top: 12px; font-size: 14px; color: #606266; }
.qr-loading, .qr-success {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 32px 0;
}
.qr-success p { font-size: 16px; font-weight: 600; color: #67C23A; }
</style>
