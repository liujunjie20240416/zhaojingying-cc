<script setup>
 import {useUserStore} from "@/stores/user.js";
 import {computed, onBeforeUnmount, ref, watch} from "vue";
 import {parseEmotionText} from "@/js/utils/emotionEmoji.js";
 import api from "@/js/http/api.js";

 const props = defineProps(['message','character'])
 const user = useUserStore()
 const contentParts = computed(() => parseEmotionText(props.message.content))
 const attachmentUrls = ref({})
 let attachmentLoadGeneration = 0

 const attachmentKey = attachment => String(attachment.id || attachment.url)
 const attachmentUrl = attachment => attachmentUrls.value[attachmentKey(attachment)] || ''

 function revokeAttachmentUrls() {
   Object.values(attachmentUrls.value).forEach(url => URL.revokeObjectURL(url))
   attachmentUrls.value = {}
 }

 async function loadAttachments(attachments = []) {
   const generation = ++attachmentLoadGeneration
   revokeAttachmentUrls()
   const loaded = {}
   await Promise.all(attachments.map(async attachment => {
     if (!attachment?.url) return
     try {
       const response = await api.get(attachment.url, {responseType: 'blob'})
       const objectUrl = URL.createObjectURL(response.data)
       if (generation !== attachmentLoadGeneration) {
         URL.revokeObjectURL(objectUrl)
         return
       }
       loaded[attachmentKey(attachment)] = objectUrl
     } catch {
       // The global request layer handles authentication. A missing or
       // unauthorized private image remains hidden instead of leaking its URL.
     }
   }))
   if (generation === attachmentLoadGeneration) attachmentUrls.value = loaded
 }

 watch(() => props.message.attachments, loadAttachments, {immediate: true, deep: true})
 onBeforeUnmount(() => {
   attachmentLoadGeneration += 1
   revokeAttachmentUrls()
 })
 const aiBubbles = computed(() => {
   if (props.message.role !== 'ai') return []
   if (Array.isArray(props.message.bubbles) && props.message.bubbles.length) {
     return props.message.bubbles
   }
   return props.message.content ? [props.message.content] : []
 })
</script>

<template>
 <div v-if="message.content || message.attachments?.length">
   <div v-if="message.role ==='ai'" class="chat chat-start items-start">
     <div class="chat-image avatar">
       <div class="w-10 rounded-full">
         <img :src="character.photo" alt=""/>
       </div>
     </div>
     <div class="ai-bubble-stack">
       <div v-for="(bubble, bubbleIndex) in aiBubbles" :key="bubbleIndex" class="chat-bubble ai-bubble whitespace-pre-wrap">
         <template v-for="(part, index) in parseEmotionText(bubble)" :key="index">
           <span v-if="part.type === 'emoji'" class="emotion-emoji" :title="part.label">{{ part.text }}</span>
           <span v-else>{{ part.text }}</span>
         </template>
       </div>
     </div>
   </div>
   <div v-else class="chat chat-end items-start">
     <div class="chat-image avatar">
       <div class="w-10 rounded-full">
         <img :src="user.photo" alt=""/>
       </div>
     </div>
     <div class="user-message-stack">
       <div v-if="message.attachments?.length" class="user-image-grid" :class="{'user-image-grid-multiple':message.attachments.length>1}">
         <a
           v-for="attachment in message.attachments"
           :key="attachment.id || attachment.url"
           :href="attachmentUrl(attachment) || undefined"
           target="_blank"
           rel="noopener"
           :aria-disabled="!attachmentUrl(attachment)"
         >
           <img v-if="attachmentUrl(attachment)" :src="attachmentUrl(attachment)" alt="用户发送的图片" loading="lazy">
           <span v-else class="image-loading"><span class="loading loading-spinner loading-sm"></span></span>
         </a>
       </div>
     <div v-if="message.content" class="chat-bubble chat-bubble-success user-bubble whitespace-pre-wrap">
       <template v-for="(part, index) in contentParts" :key="index">
         <span v-if="part.type === 'emoji'" class="emotion-emoji" :title="part.label">{{ part.text }}</span>
         <span v-else>{{ part.text }}</span>
       </template>
     </div>
     </div>
   </div>
 </div>
</template>

<style scoped>
.chat-image {
  align-self: flex-start !important;
}

.ai-bubble::before {
  top: 14px !important;
  bottom: auto !important;
  transform: rotateY(0deg) !important;
}

.ai-bubble-stack {
  grid-column: 2;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  width: fit-content;
  min-width: 0;
  max-width: 90%;
  gap: 4px;
}

.ai-bubble,
.user-bubble {
  min-width: 4rem;
  max-width: 100%;
  word-break: normal;
  overflow-wrap: anywhere;
}

.ai-bubble-stack .ai-bubble:not(:first-child)::before {
  display: none;
}

.user-bubble::before {
  top: 14px !important;
  bottom: auto !important;
  transform: rotateY(180deg) !important;
}
.user-message-stack{grid-column:1;display:flex;flex-direction:column;align-items:flex-end;width:fit-content;min-width:0;max-width:78%;gap:4px}.user-image-grid{display:grid;grid-template-columns:1fr;gap:4px;overflow:hidden;border-radius:14px}.user-image-grid-multiple{grid-template-columns:repeat(2,minmax(0,1fr))}.user-image-grid img{display:block;width:150px;height:150px;object-fit:cover;background:rgba(0,0,0,.2)}.user-image-grid-multiple img{width:96px;height:96px}
.image-loading{display:grid;width:150px;height:150px;place-items:center;background:rgba(0,0,0,.12)}.user-image-grid-multiple .image-loading{width:96px;height:96px}

.emotion-emoji {
  display: inline-block;
  font-size: 1.25em;
  line-height: 1;
  vertical-align: -0.1em;
}

</style>
