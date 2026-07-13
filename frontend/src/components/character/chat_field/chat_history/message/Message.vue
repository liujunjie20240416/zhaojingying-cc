<script setup>
 import {useUserStore} from "@/stores/user.js";
 import {computed} from "vue";
 import {parseEmotionText} from "@/js/utils/emotionEmoji.js";
 import CONFIG_API from "@/js/config/config.js";

 const props = defineProps(['message','character'])
 const user = useUserStore()
 const contentParts = computed(() => parseEmotionText(props.message.content))
 const mediaUrl = url => (!url || /^https?:\/\//.test(url)) ? url : `${CONFIG_API.HTTP_URL || ''}${url}`
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
         <a v-for="attachment in message.attachments" :key="attachment.id || attachment.url" :href="mediaUrl(attachment.url)" target="_blank" rel="noopener"><img :src="mediaUrl(attachment.url)" alt="用户发送的图片" loading="lazy"></a>
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

.emotion-emoji {
  display: inline-block;
  font-size: 1.25em;
  line-height: 1;
  vertical-align: -0.1em;
}

</style>
