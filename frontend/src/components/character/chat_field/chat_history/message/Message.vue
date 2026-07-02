<script setup>
 import {useUserStore} from "@/stores/user.js";
 import {computed} from "vue";
 import {parseEmotionText} from "@/js/utils/emotionEmoji.js";

 const props = defineProps(['message','character'])
 const user = useUserStore()
 const contentParts = computed(() => parseEmotionText(props.message.content))
</script>

<template>
 <div v-if="message.content">
   <div v-if="message.role ==='ai'" class="chat chat-start items-start">
     <div class="chat-image avatar">
       <div class="w-10 rounded-full">
         <img :src="character.photo" alt=""/>
       </div>
     </div>
     <div class="chat-bubble ai-bubble whitespace-pre-wrap break-all">
       <template v-for="(part, index) in contentParts" :key="index">
         <span v-if="part.type === 'emoji'" class="emotion-emoji" :title="part.label">{{ part.text }}</span>
         <span v-else>{{ part.text }}</span>
       </template>
     </div>
   </div>
   <div v-else class="chat chat-end items-start">
     <div class="chat-image avatar">
       <div class="w-10 rounded-full">
         <img :src="user.photo" alt=""/>
       </div>
     </div>
     <div class="chat-bubble chat-bubble-success user-bubble whitespace-pre-wrap break-all">
       <template v-for="(part, index) in contentParts" :key="index">
         <span v-if="part.type === 'emoji'" class="emotion-emoji" :title="part.label">{{ part.text }}</span>
         <span v-else>{{ part.text }}</span>
       </template>
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

.user-bubble::before {
  top: 14px !important;
  bottom: auto !important;
  transform: rotateY(180deg) !important;
}

.emotion-emoji {
  display: inline-block;
  font-size: 1.25em;
  line-height: 1;
  vertical-align: -0.1em;
}

</style>
