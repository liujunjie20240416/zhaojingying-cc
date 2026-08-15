<script setup>
import {computed, nextTick, onUnmounted, ref, useTemplateRef} from "vue";
import InputField from "@/components/character/chat_field/input_field/InputField.vue";
import CharacterPhotoField from "@/components/character/chat_field/character_photo_field/CharacterPhotoField.vue";
import ChatHistory from "@/components/character/chat_field/chat_history/ChatHistory.vue";
import MemoryManager from "@/components/character/chat_field/MemoryManager.vue";
import ContextMonitor from "@/components/character/chat_field/ContextMonitor.vue";
import RemoveIcon from "@/components/character/icons/RemoveIcon.vue";
import api from "@/js/http/api.js";
import {getApiErrorMessage} from "@/js/http/errors.js";

const props = defineProps(['friend'])
const modalRef = useTemplateRef('modal-ref')
const inputRef = useTemplateRef('input-ref')
const history = ref([])
const chatHistoryRef = useTemplateRef('chat-history-ref')
const clearing = ref(false)
const showMemoryManager = ref(false)
const showContextMonitor = ref(false)
const isOnline = ref(true)
const chatError = ref('')
const isTyping = computed(() => {
  return history.value.some(message => message.role === 'ai' && message.isTyping)
})
const bubbleTimers = new Map()
const pendingBubbleStates = new Map()

function clearBubbleTimers(responseId) {
  if (responseId) {
    ;(bubbleTimers.get(responseId) || []).forEach(timer => window.clearTimeout(timer))
    bubbleTimers.delete(responseId)
    return
  }
  bubbleTimers.forEach(timers => timers.forEach(timer => window.clearTimeout(timer)))
  bubbleTimers.clear()
}

function flushPendingBubbles(responseId) {
  const pendingBubbleState = pendingBubbleStates.get(responseId)
  if (!pendingBubbleState) return
  clearBubbleTimers(responseId)
  const {message, bubbles} = pendingBubbleState
  message.bubbles = [...bubbles]
  message.content = bubbles.join('\n')
  message.isTyping = false
  pendingBubbleStates.delete(responseId)
}

function findResponseMessage(responseId) {
  return history.value.find(message => message.id === responseId && message.role === 'ai')
}

function bubbleDelay(previousBubble) {
  // Short replies feel like separate IM messages without making a longer
  // response painfully slow. Delay is measured before the next bubble.
  return Math.min(2000, 700 + String(previousBubble || '').length * 28)
}

async function showModal() {
  modalRef.value.showModal()
  await nextTick()
  inputRef.value.focus()
}

const modalStyle = computed(() => {
  if (props.friend) {
    return {
      backgroundImage: `url(${props.friend.character.background_image})`,
      backgroundSize: 'cover',
      backgroundPosition: 'center',
      backgroundRepeat: 'no-repeat',
    }
  } else {
    return {}
  }
})

function handlePushBackMessage(msg){
  history.value.push(msg)
  chatHistoryRef.value.scrollToBottom()
}
function handleAddToLastMessage({responseId, delta}){
  const targetMessage = findResponseMessage(responseId)
  if (!targetMessage) return
  targetMessage.isTyping = false
  targetMessage.content += delta
  isOnline.value = true
  chatHistoryRef.value.scrollToBottom()
}
function handleSetLastMessageBubbles({responseId, bubbles}){
  const targetMessage = findResponseMessage(responseId)
  if (!targetMessage) return
  flushPendingBubbles(responseId)
  const normalized = Array.isArray(bubbles)
    ? bubbles.map(item => String(item).trim()).filter(Boolean)
    : []
  if (!normalized.length) {
    targetMessage.isTyping = false
    return
  }

  targetMessage.bubbles = [normalized[0]]
  targetMessage.content = normalized[0]
  targetMessage.isTyping = normalized.length > 1
  if (normalized.length > 1) {
    const pendingBubbleState = {message: targetMessage, bubbles: normalized}
    pendingBubbleStates.set(responseId, pendingBubbleState)
    const timers = []
    let elapsed = 0
    for (let index = 1; index < normalized.length; index += 1) {
      elapsed += bubbleDelay(normalized[index - 1])
      const timer = window.setTimeout(() => {
        if (pendingBubbleStates.get(responseId) !== pendingBubbleState) return
        targetMessage.bubbles.push(normalized[index])
        targetMessage.content = targetMessage.bubbles.join('\n')
        targetMessage.isTyping = index < normalized.length - 1
        chatHistoryRef.value?.scrollToBottom()
        if (index === normalized.length - 1) {
          pendingBubbleStates.delete(responseId)
          bubbleTimers.delete(responseId)
        }
      }, elapsed)
      timers.push(timer)
    }
    bubbleTimers.set(responseId, timers)
  }
  isOnline.value = true
  chatHistoryRef.value.scrollToBottom()
}
function handleReplyProvenance({responseId, provenance}) {
  const targetMessage = findResponseMessage(responseId)
  if (targetMessage) targetMessage.replyProvenance = provenance || {}
}
function handleConnectionOnline() {
  isOnline.value = true
  chatError.value = ''
}
function handleConnectionError({responseId, message}) {
  const targetMessage = findResponseMessage(responseId)
  if (targetMessage) {
    targetMessage.isTyping = false
    // 尚未收到任何回复内容 → 移除占位气泡，避免"空回复"残留在历史里
    if (!targetMessage.content && !(targetMessage.bubbles || []).length) {
      const index = history.value.indexOf(targetMessage)
      if (index !== -1) history.value.splice(index, 1)
    }
  }
  isOnline.value = false
  chatError.value = message || '连接失败，请重试'
}
function handleTypingFinished({responseId}) {
  const targetMessage = findResponseMessage(responseId)
  if (targetMessage && !pendingBubbleStates.has(responseId)) {
    targetMessage.isTyping = false
  }
}
function handlePushFrontMessage(msg){
  history.value.unshift(msg)
}
function handleClose() {
  inputRef.value.close()
  showMemoryManager.value = false
  showContextMonitor.value = false
}

async function handleClearHistory() {
  if (!confirm(
    '确定清除与这个角色的所有在线聊天吗？\n\n' +
    '会删除：聊天原文、聊天图片、AI 从这些聊天自动提炼的记忆。\n' +
    '会保留：导入聊天产生的记忆、你手动添加或确认的记忆。'
  )) return
  clearing.value = true
  chatError.value = ''
  try {
    const res = await api.post('/api/friend/message/clear/', {
      friend_id: props.friend.id,
    })
    if (res.data.result === 'success') {
      inputRef.value?.cancelAllRequests()
      clearBubbleTimers()
      pendingBubbleStates.clear()
      history.value = []
      clearing.value = false
    }
  } catch (err) {
    chatError.value = getApiErrorMessage(err, '聊天记录清除失败，请稍后重试')
    clearing.value = false
  }
}

onUnmounted(clearBubbleTimers)

defineExpose({
  showModal,
})
</script>

<template>
  <dialog ref="modal-ref" class="modal" @close="handleClose">
    <div class="modal-box chat-modal-box" :style="modalStyle">
      <div v-if="chatError" class="absolute left-3 right-3 top-16 z-30 rounded-lg bg-error/90 px-3 py-2 text-xs text-error-content">{{ chatError }}</div>
      <div class="chat-top-bar">
        <div v-if="friend" class="chat-status-bar">
          <div class="status-avatar">
            <img :src="friend.character.photo" alt="">
          </div>
          <div class="min-w-0">
            <div class="status-name">{{ friend.character.name }}</div>
            <div class="status-line" :class="{'status-line-offline': !isOnline}">
              <span class="status-dot" :class="{'status-dot-offline': !isOnline}"></span>
              <span>{{ isOnline ? '在线' : '不在线' }}</span>
            </div>
          </div>
        </div>
        <div class="typing-indicator" :class="{'typing-indicator-visible': isTyping}">
          <template v-if="isTyping">
            <span class="typing-mini-dot"></span>
            <span>正在输入...</span>
          </template>
        </div>
        <button
          @click="handleClearHistory"
          class="top-icon-button"
          :disabled="clearing"
          title="清除对话历史"
        >
          <span v-if="clearing" class="loading loading-spinner loading-xs"></span>
          <RemoveIcon v-else />
        </button>
        <button @click="showMemoryManager = true" class="top-icon-button" title="管理记忆">🧠</button>
        <button @click="showContextMonitor = true" class="top-icon-button" title="上下文监控">◫</button>
        <button @click="modalRef.close()" class="top-icon-button close-button" title="关闭">✕</button>
      </div>
      <MemoryManager v-if="friend && showMemoryManager" :friend-id="friend.id" @close="showMemoryManager = false" />
      <ContextMonitor v-if="friend && showContextMonitor" :friend-id="friend.id" @close="showContextMonitor = false" />
      <ChatHistory ref="chat-history-ref" v-if="friend" :history="history" :friendId="friend.id" :character="friend.character" @pushFrontMessage="handlePushFrontMessage" />
      <InputField
          v-if="friend"
          ref="input-ref"
          :friendId=friend.id
          @pushBackMessage="handlePushBackMessage"
          @addToLastMessage="handleAddToLastMessage"
          @setLastMessageBubbles="handleSetLastMessageBubbles"
          @replyProvenance="handleReplyProvenance"
          @connectionOnline="handleConnectionOnline"
          @connectionError="handleConnectionError"
          @typingFinished="handleTypingFinished"
      />
      <CharacterPhotoField v-if="friend" :character="friend.character" />
    </div>
  </dialog>
</template>

<style scoped>
.chat-top-bar {
  position: absolute;
  top: 8px;
  left: 10px;
  right: 10px;
  z-index: 8;
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 48px;
  padding: 5px 6px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 20px;
  background: rgba(0, 0, 0, 0.2);
  color: white;
  backdrop-filter: blur(12px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

.chat-modal-box {
  width: min(100vw, 360px);
  max-width: none;
  height: min(100dvh, 600px);
  max-height: none;
  padding: 0;
  overflow: hidden;
}

.chat-status-bar {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 112px;
  min-width: 0;
}

.status-avatar {
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  overflow: hidden;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.3);
}

.status-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.status-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.2;
}

.status-line {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
  color: rgba(255, 255, 255, 0.72);
  font-size: 11px;
  line-height: 1.1;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: rgba(134, 239, 172, 0.9);
  box-shadow: 0 0 8px rgba(134, 239, 172, 0.5);
}

.status-line-offline {
  color: rgba(255, 255, 255, 0.5);
}

.status-line-typing {
  color: rgba(187, 247, 208, 0.95);
}

.status-dot-offline {
  background: rgba(148, 163, 184, 0.9);
  box-shadow: none;
}

.typing-indicator {
  display: flex;
  flex: 1 1 auto;
  min-width: 76px;
  align-items: center;
  gap: 5px;
  color: rgba(187, 247, 208, 0);
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
  transition: color 0.16s ease;
}

.typing-indicator-visible {
  color: rgba(187, 247, 208, 0.95);
}


.typing-mini-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: rgba(187, 247, 208, 0.95);
  animation: status-pulse 1.1s ease-in-out infinite;
}

.top-icon-button {
  display: flex;
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  color: rgba(255, 255, 255, 0.88);
  background: rgba(255, 255, 255, 0.08);
  cursor: pointer;
  transition: background 0.16s ease, color 0.16s ease, transform 0.16s ease;
}

.top-icon-button:hover {
  color: white;
  background: rgba(255, 255, 255, 0.16);
  transform: translateY(-1px);
}

.top-icon-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
  transform: none;
}

.close-button {
  font-size: 20px;
  line-height: 1;
}

@keyframes status-pulse {
  0%, 100% {
    opacity: 0.45;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
}

@media (max-width: 639px) {
  .chat-modal-box {
    width: 100vw;
    height: 100dvh;
    border-radius: 0;
  }

  .chat-status-bar {
    width: auto;
    flex: 1 1 auto;
  }

  .typing-indicator {
    display: none;
  }
}

</style>
