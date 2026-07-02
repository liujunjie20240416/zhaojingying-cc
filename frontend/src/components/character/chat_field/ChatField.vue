<script setup>
import {computed, nextTick, ref, useTemplateRef} from "vue";
import InputField from "@/components/character/chat_field/input_field/InputField.vue";
import CharacterPhotoField from "@/components/character/chat_field/character_photo_field/CharacterPhotoField.vue";
import ChatHistory from "@/components/character/chat_field/chat_history/ChatHistory.vue";
import MemoryManager from "@/components/character/chat_field/MemoryManager.vue";
import RemoveIcon from "@/components/character/icons/RemoveIcon.vue";
import api from "@/js/http/api.js";

const props = defineProps(['friend'])
const modalRef = useTemplateRef('modal-ref')
const inputRef = useTemplateRef('input-ref')
const history = ref([])
const chatHistoryRef = useTemplateRef('chat-history-ref')
const clearing = ref(false)
const showMemoryManager = ref(false)
const isOnline = ref(true)
const isTyping = computed(() => {
  const lastMessage = history.value.at(-1)
  return Boolean(lastMessage?.role === 'ai' && lastMessage?.isTyping)
})

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
function handleAddToLastMessage(delta){
  const lastMessage = history.value.at(-1)
  lastMessage.isTyping = false
  lastMessage.content += delta
  isOnline.value = true
  chatHistoryRef.value.scrollToBottom()
}
function handleConnectionOnline() {
  isOnline.value = true
}
function handleConnectionError() {
  const lastMessage = history.value.at(-1)
  if (lastMessage?.role === 'ai') {
    lastMessage.isTyping = false
  }
  isOnline.value = false
}
function handleTypingFinished() {
  const lastMessage = history.value.at(-1)
  if (lastMessage?.role === 'ai') {
    lastMessage.isTyping = false
  }
}
function handlePushFrontMessage(msg){
  history.value.unshift(msg)
}
function handleClose() {
  inputRef.value.close()
  showMemoryManager.value = false
}

async function handleClearHistory() {
  if (!confirm('确定清除所有对话历史吗？')) return
  clearing.value = true
  try {
    const res = await api.post('/api/friend/message/clear/', {
      friend_id: props.friend.id,
    })
    if (res.data.result === 'success') {
      history.value = []
      clearing.value = false
    }
  } catch (err) {
    clearing.value = false
  }
}

defineExpose({
  showModal,
})
</script>

<template>
  <dialog ref="modal-ref" class="modal" @close="handleClose">
    <div class="modal-box w-90 h-150" :style="modalStyle">
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
        <button @click="modalRef.close()" class="top-icon-button close-button" title="关闭">✕</button>
      </div>
      <MemoryManager v-if="friend && showMemoryManager" :friend-id="friend.id" @close="showMemoryManager = false" />
      <ChatHistory ref="chat-history-ref" v-if="friend" :history="history" :friendId="friend.id" :character="friend.character" @pushFrontMessage="handlePushFrontMessage" />
      <InputField
          v-if="friend"
          ref="input-ref"
          :friendId=friend.id
          @pushBackMessage="handlePushBackMessage"
          @addToLastMessage="handleAddToLastMessage"
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

</style>
