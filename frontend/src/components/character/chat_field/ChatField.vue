<script setup>
import {computed, nextTick, ref, useTemplateRef} from "vue";
import InputField from "@/components/character/chat_field/input_field/InputField.vue";
import CharacterPhotoField from "@/components/character/chat_field/character_photo_field/CharacterPhotoField.vue";
import ChatHistory from "@/components/character/chat_field/chat_history/ChatHistory.vue";
import MemoryManager from "@/components/character/chat_field/MemoryManager.vue";
import api from "@/js/http/api.js";

const props = defineProps(['friend'])
const modalRef = useTemplateRef('modal-ref')
const inputRef = useTemplateRef('input-ref')
const history = ref([])
const chatHistoryRef = useTemplateRef('chat-history-ref')
const clearing = ref(false)
const showMemoryManager = ref(false)

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
  history.value.at(-1).content+=delta
  chatHistoryRef.value.scrollToBottom()
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
      <div class="absolute right-1 top-1 flex gap-1">
        <button
          @click="handleClearHistory"
          class="btn btn-sm btn-circle btn-ghost bg-transparent"
          :disabled="clearing"
          title="清除对话历史"
        >
          <span v-if="clearing" class="loading loading-spinner loading-xs"></span>
          <span v-else>🗑</span>
        </button>
        <button @click="showMemoryManager = true" class="btn btn-sm btn-circle btn-ghost bg-transparent" title="管理记忆">🧠</button>
        <button @click="modalRef.close()" class="btn btn-sm btn-circle btn-ghost bg-transparent">✕</button>
      </div>
      <MemoryManager v-if="friend && showMemoryManager" :friend-id="friend.id" @close="showMemoryManager = false" />
      <ChatHistory ref="chat-history-ref" v-if="friend" :history="history" :friendId="friend.id" :character="friend.character" @pushFrontMessage="handlePushFrontMessage" />
      <InputField v-if="friend" ref="input-ref" :friendId=friend.id @pushBackMessage="handlePushBackMessage" @addToLastMessage="handleAddToLastMessage" />
      <CharacterPhotoField v-if="friend" :character="friend.character" />
    </div>
  </dialog>
</template>

<style scoped>

</style>
