<script setup>
import {onMounted, onUnmounted, ref, watch} from "vue";
import api from "@/js/http/api.js";
import {getApiErrorMessage} from "@/js/http/errors.js";

const props = defineProps({
  characterId: {type: [Number, String], required: true},
  characterName: {type: String, default: ''},
  memoryVisibility: {type: String, default: 'private'},
})
const emit = defineEmits(['imported', 'visibilityChanged'])

const file = ref(null)
const targetName = ref('')
const uploading = ref(false)
const message = ref('')
const isSuccess = ref(false)
const preprocessing = ref(false)
const preprocessingDone = ref(false)
const progressPct = ref(0)
const canResume = ref(false)
const visibility = ref(props.memoryVisibility || 'private')
const savingVisibility = ref(false)
const privacyMessage = ref('')
let statusTimer = null

watch(() => props.memoryVisibility, value => {
  if (value === 'private' || value === 'public') visibility.value = value
})

async function setVisibility(nextVisibility) {
  if (nextVisibility === visibility.value || savingVisibility.value) return
  if (nextVisibility === 'public' && !window.confirm(
    '设为公开后，其他使用这个角色的用户可以检索导入聊天原文、共同记忆和关系信息。确定公开吗？'
  )) return

  const previous = visibility.value
  savingVisibility.value = true
  privacyMessage.value = ''
  try {
    const res = await api.post('/api/create/character/imported-memory-visibility/', {
      character_id: Number(props.characterId),
      visibility: nextVisibility,
    })
    if (res.data.result !== 'success') throw new Error(res.data.result)
    visibility.value = res.data.visibility
    privacyMessage.value = visibility.value === 'private'
      ? '已设为私人，仅你可以使用导入原文和派生记忆。'
      : '已设为公开，其他使用该角色的用户也可以使用导入记忆。'
    emit('visibilityChanged', visibility.value)
  } catch (err) {
    visibility.value = previous
    privacyMessage.value = getApiErrorMessage(err, '隐私设置保存失败')
  } finally {
    savingVisibility.value = false
  }
}

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
      canResume.value = false
      progressPct.value = Number(data.progress_pct || 0)
      const stageText = data.stage === 'writing'
        ? '正在写入记忆...'
        : data.stage === 'style_reduce'
          ? '正在重新学习完整说话风格...'
          : data.stage === 'relationship_reduce'
            ? '正在总结关系演变...'
            : '正在预处理记忆...'
      const chunkText = data.total_chunks
        ? `（${data.completed_chunks || 0}/${data.total_chunks} 个 Chunk）`
        : ''
      message.value = `聊天原文导入完成，${stageText} ${progressPct.value}% ${chunkText}`
      isSuccess.value = true
      preprocessing.value = true
      return
    }

    if (data.status === 'done') {
      canResume.value = false
      progressPct.value = 100
      preprocessing.value = false
      preprocessingDone.value = true
      message.value = `预处理完成！共分析 ${data.total_messages || 0} 条消息`
      isSuccess.value = true
      stopPolling()
      emit('imported')
      return
    }

    if (data.status === 'failed' || data.status === 'partial') {
      preprocessing.value = false
      preprocessingDone.value = false
      const progress = data.total_chunks
        ? `已成功 ${data.completed_chunks || 0}/${data.total_chunks} 个 Chunk。`
        : ''
      message.value = `${data.status === 'partial' ? '预处理未完成' : '预处理失败'}：${progress}${data.error_message || '未知错误'}`
      isSuccess.value = false
      canResume.value = true
      stopPolling()
      return
    }
  } catch (err) {
    preprocessing.value = false
    message.value = getApiErrorMessage(
      err,
      '无法连接后端服务，请确认 API 服务已在 127.0.0.1:8000 启动',
    )
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
  canResume.value = false
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
    message.value = getApiErrorMessage(err, '导入失败，请重试')
    isSuccess.value = false
  } finally {
    uploading.value = false
  }
}

async function handleResume() {
  message.value = '正在从已完成的聊天片段继续...'
  isSuccess.value = true
  canResume.value = false
  try {
    const res = await api.post('/api/import/resume/', {
      character_id: props.characterId,
    })
    if (res.data.result !== 'success') {
      message.value = res.data.result || '恢复失败，请稍后重试'
      isSuccess.value = false
      canResume.value = true
      return
    }
    preprocessing.value = true
    startPolling()
  } catch (err) {
    message.value = getApiErrorMessage(err, '恢复失败，请稍后重试')
    isSuccess.value = false
    canResume.value = true
  }
}

onMounted(async () => {
  await pollImportStatus()
  if (preprocessing.value) {
    statusTimer = setInterval(pollImportStatus, 2000)
  }
})

onUnmounted(stopPolling)
</script>

<template>
<div class="card bg-base-100 border border-base-300 mt-4">
  <div class="card-body p-4">
    <h4 class="card-title text-base">导入聊天记录</h4>
    <p class="text-sm text-base-content/60">
      上传微信聊天记录 TXT 文件，AI 将学会{{ characterName || '她' }}的说话方式。
    </p>
    <div class="alert alert-info py-2 text-xs">
      重新导入会完整替换旧的导入原文、导入索引和导入记忆，并根据新的全部记录重新生成说话风格；后续与 AI 的在线聊天不会被删除。
    </div>

    <div class="rounded-box border border-base-300 p-3">
      <div class="mb-2 text-sm font-semibold">导入记忆隐私</div>
      <div class="join w-full">
        <button
          type="button"
          :class="['btn btn-sm join-item flex-1', visibility === 'private' ? 'btn-neutral' : 'btn-outline']"
          :disabled="savingVisibility"
          @click="setVisibility('private')"
        >
          🔒 私人
        </button>
        <button
          type="button"
          :class="['btn btn-sm join-item flex-1', visibility === 'public' ? 'btn-warning' : 'btn-outline']"
          :disabled="savingVisibility"
          @click="setVisibility('public')"
        >
          🌐 公开
        </button>
      </div>
      <p class="mt-2 text-xs text-base-content/65">
        私人：仅你可使用原文、共同记忆和关系分析；公开：所有使用该角色的用户均可检索。说话风格不受此开关影响。
      </p>
      <p v-if="privacyMessage" class="mt-2 text-xs">{{ privacyMessage }}</p>
    </div>

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
      <button
        v-if="canResume"
        @click="handleResume"
        class="btn btn-sm btn-outline"
      >
        从断点继续
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
      导入、记忆预处理和说话风格更新都已完成。
    </div>
  </div>
</div>
</template>
