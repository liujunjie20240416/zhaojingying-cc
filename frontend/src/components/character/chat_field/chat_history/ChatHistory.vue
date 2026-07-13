<script setup>

import Message from "@/components/character/chat_field/chat_history/message/Message.vue";
import {computed, nextTick, onBeforeUnmount, onMounted, useTemplateRef} from "vue";
import api from "@/js/http/api.js";
import {formatChatTime, shouldShowTime} from "@/js/utils/chatTime.js";
const props=defineProps(['history','friendId','character'])
const scrollRef = useTemplateRef('scroll-ref')
const sentinelRef = useTemplateRef('sentinel-ref')
const emit = defineEmits(['pushFrontMessage'])
let isLoading = false
let hasMessages = true
let lastMessageId = 0

const displayItems = computed(() => {
  const items = []
  let previousTime = null

  for (const message of props.history) {
    if (shouldShowTime(message.createdAt, previousTime)) {
      items.push({
        type: 'time',
        id: `time-${message.id}`,
        text: formatChatTime(message.createdAt),
      })
    }

    items.push({
      type: 'message',
      id: message.id,
      message,
    })

    if (message.createdAt) {
      previousTime = message.createdAt
    }
  }

  return items
})

function checkSentinelVisible() {  // 判断哨兵是否能被看到
  if (!sentinelRef.value) return false

  const sentinelRect = sentinelRef.value.getBoundingClientRect()
  const scrollRect = scrollRef.value.getBoundingClientRect()
  return sentinelRect.top < scrollRect.bottom && sentinelRect.bottom > scrollRect.top
}


async function loadMore(){
  if(isLoading||!hasMessages) return
  isLoading=true
  let newMessages=[]
  try{
    const res = await api.get('/api/friend/message/get_history/',{
      params:{
        last_message_id:lastMessageId,
        friend_id:props.friendId,
      }
    })
    const data = res.data
    if(data.result ==='success'){
      newMessages=data.messages
    }
  }catch(err){

  }finally{
    isLoading=false
    if(newMessages.length===0){
      hasMessages=false
    }else{
      const oldHeight = scrollRef.value.scrollHeight
      const oldTop = scrollRef.value.scrollTop
      for(const m of newMessages){
        emit('pushFrontMessage',{
          role:'ai',
          content:m.output,
          bubbles:m.output_bubbles || (m.output ? [m.output] : []),
          id:crypto.randomUUID(),
          createdAt:m.create_time,
        })
        emit('pushFrontMessage',{
          role:'user',
          content:m.user_message,
          attachments:m.attachments || [],
          id:crypto.randomUUID(),
          createdAt:m.create_time,
        })
        lastMessageId = m.id
      }
      await nextTick()

      const newHeight = scrollRef.value.scrollHeight
      scrollRef.value.scrollTop = oldTop+newHeight-oldHeight

      if(checkSentinelVisible()){
        await loadMore()
      }
    }
  }
}

let observer = null
onMounted(async () => {
  await loadMore()

  observer = new IntersectionObserver(
    entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          loadMore()
        }
      })
    },
    {root: null, rootMargin: '2px', threshold: 0}
  )

  observer.observe(sentinelRef.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
})

async function scrollToBottom(){
  await nextTick()
  scrollRef.value.scrollTop= scrollRef.value.scrollHeight
}

defineExpose({
  scrollToBottom,
})
</script>

<template>
 <div ref="scroll-ref" class="chat-history absolute overflow-y-scroll no-scrollbar">
   <div ref="sentinel-ref" class="h-2 "></div>
   <template v-for="item in displayItems" :key="item.id">
     <div v-if="item.type === 'time'" class="chat-time-separator">
       {{ item.text }}
     </div>
     <Message
         v-else
         :message="item.message"
         :character="character"
     />
   </template>
 </div>
</template>

<style scoped>
/* 隐藏 Chrome, Safari 和 Opera 的滚动条 */
.no-scrollbar::-webkit-scrollbar {
  display: none;
}

/* 隐藏 IE, Edge 和 Firefox 的滚动条 */
.no-scrollbar {
  -ms-overflow-style: none; /* IE and Edge */
  scrollbar-width: none; /* Firefox */
}

.chat-history {
  top: 72px;
  right: 0;
  bottom: 76px;
  left: 0;
}

.chat-time-separator {
  width: fit-content;
  max-width: 80%;
  margin: 10px auto 8px;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.22);
  color: rgba(255, 255, 255, 0.82);
  font-size: 11px;
  line-height: 1.4;
  backdrop-filter: blur(6px);
}
</style>
