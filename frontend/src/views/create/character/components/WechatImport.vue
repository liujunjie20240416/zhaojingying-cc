<script setup>
import {onUnmounted, ref} from "vue";
import api from "@/js/http/api.js";

const props = defineProps(['characterId', 'characterName'])
const emit = defineEmits(['imported'])

const file = ref(null)
const targetName = ref('')
const uploading = ref(false)
const message = ref('')
const isSuccess = ref(false)
const preprocessing = ref(false)
const preprocessingDone = ref(false)
const progressPct = ref(0)
let statusTimer = null

function stopPolling() {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
}

function handleFileChange(e) {
  file.value = e.target.files[0] || null
  message.value = ''
  isSuccess.value = false
  preprocessing.value = false
  preprocessingDone.value = false
  progressPct.value = 0
  stopPolling()
}

async function pollImportStatus() {
  try {
    const res = await api.get('/api/import/status/', {
      params: {character_id: props.characterId},
    })
    const data = res.data

    if (data.result !== 'success') {
      message.value = data.result || '预处理状态查询失败'
      isSuccess.value = false
      preprocessing.value = false
      stopPolling()
      return
    }

    if (data.status === 'analyzing') {
      progressPct.value = Number(data.progress_pct || 0)
      const stageText = progressPct.value >= 98
        ? '正在写入记忆...'
        : progressPct.value >= 96
          ? '正在总结关系演变...'
          : '正在预处理记忆...'
      message.value = `聊天原文导入完成，${stageText} ${progressPct.value}%`
      isSuccess.value = true
      preprocessing.value = true
      return
    }

    if (data.status === 'done') {
      progressPct.value = 100
      preprocessing.value = false
      preprocessingDone.value = true
      message.value = `预处理完成！共分析 ${data.total_messages || 0} 条消息`
      isSuccess.value = true
      stopPolling()
      emit('imported')
      return
    }

    if (data.status === 'failed') {
      preprocessing.value = false
      preprocessingDone.value = false
      message.value = `预处理失败：${data.error_message || '未知错误'}`
      isSuccess.value = false
      stopPolling()
      return
    }
  } catch (err) {
    preprocessing.value = false
    message.value = '预处理状态查询失败，请稍后重试'
    isSuccess.value = false
    stopPolling()
  }
}

function startPolling() {
  stopPolling()
  pollImportStatus()
  statusTimer = setInterval(pollImportStatus, 2000)
}

async function handleImport() {
  if (!file.value) {
    message.value = '请选择聊天记录文件'
    isSuccess.value = false
    return
  }
  if (!targetName.value.trim()) {
    message.value = '请输入对方的名字（如：大白鹅）'
    isSuccess.value = false
    return
  }

  uploading.value = true
  message.value = '正在解析...'
  isSuccess.value = false
  preprocessing.value = false
  preprocessingDone.value = false
  progressPct.value = 0
  stopPolling()

  try {
    const formData = new FormData()
    formData.append('file', file.value)
    formData.append('target_name', targetName.value.trim())
    formData.append('character_id', props.characterId)

    const res = await api.post('/api/import/wechat/', formData)
    const data = res.data

    if (data.result === 'success') {
      message.value = `聊天原文导入完成！共 ${data.total_messages} 条消息，正在预处理记忆...`
      isSuccess.value = true
      preprocessing.value = true
      startPolling()
    } else {
      message.value = data.result
      isSuccess.value = false
    }
  } catch (err) {
    message.value = '导入失败，请重试'
    isSuccess.value = false
  } finally {
    uploading.value = false
  }
}

onUnmounted(stopPolling)
</script>

<template>
<div class="card bg-base-100 border border-base-300 mt-4">
  <div class="card-body p-4">
    <h4 class="card-title text-base">导入聊天记录</h4>
    <p class="text-sm text-base-content/60">
      上传微信聊天记录 TXT 文件，AI 将学会{{ characterName || '她' }}的说话方式。
    </p>

    <div class="form-control">
      <label class="label py-1">
        <span class="label-text text-sm">对方的名字</span>
      </label>
      <input
        v-model="targetName"
        type="text"
        placeholder="如：大白鹅"
        class="input input-bordered input-sm w-full max-w-xs"
        :disabled="uploading"
      />
    </div>

    <div class="form-control">
      <label class="label py-1">
        <span class="label-text text-sm">聊天记录文件（.txt）</span>
      </label>
      <input
        type="file"
        accept=".txt,.csv,.html"
        class="file-input file-input-bordered file-input-sm w-full max-w-xs"
        :disabled="uploading"
        @change="handleFileChange"
      />
    </div>

    <div class="flex items-center gap-2 mt-2">
      <button
        @click="handleImport"
        class="btn btn-sm btn-primary"
        :disabled="uploading"
      >
        <span v-if="uploading" class="loading loading-spinner loading-xs"></span>
        {{ uploading ? '导入中...' : '开始导入' }}
      </button>
    </div>

    <div
      v-if="message"
      :class="['alert text-sm mt-2 p-2', isSuccess ? 'alert-success' : 'alert-warning']"
    >
      <span>{{ message }}</span>
    </div>

    <progress
      v-if="preprocessing"
      class="progress progress-primary w-full mt-2"
      :value="progressPct"
      max="100"
    ></progress>

    <div v-if="preprocessingDone" class="text-xs text-success mt-2">
      导入和预处理都已完成。
    </div>
  </div>
</div>
</template>
