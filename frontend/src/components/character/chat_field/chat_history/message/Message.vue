<script setup>
 import {useUserStore} from "@/stores/user.js";
 import {computed, onBeforeUnmount, ref, watch} from "vue";
 import {parseEmotionText} from "@/js/utils/emotionEmoji.js";
 import api from "@/js/http/api.js";

 const props = defineProps(['message','character'])
 const user = useUserStore()
 const contentParts = computed(() => parseEmotionText(props.message.content))
 const attachmentUrls = ref({})
 const showProvenance = ref(false)
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
 const provenance = computed(() => props.message.replyProvenance || {})
 const retrievedRaw = computed(() => Array.isArray(provenance.value.retrieved_raw) ? provenance.value.retrieved_raw : [])
 const semanticFacts = computed(() => Array.isArray(provenance.value.semantic_facts) ? provenance.value.semantic_facts : [])
 const recentOnline = computed(() => {
   const retrievedOnlineIds = new Set(
     retrievedRaw.value
       .filter(source => source?.source_type === 'online_chat')
       .flatMap(source => Array.isArray(source.message_refs) ? source.message_refs : [])
       .map(id => String(id)),
   )
   return (Array.isArray(provenance.value.recent_online) ? provenance.value.recent_online : [])
     .filter(row => !retrievedOnlineIds.has(String(row.message_id)))
 })
 const sourceLabel = source => source === 'import_chat' ? '导入聊天记录' : 'Online Chat 原文'
 function openProvenance() {
   if (props.message.role === 'ai') showProvenance.value = true
 }
</script>

<template>
 <div v-if="message.content || message.attachments?.length">
   <div v-if="message.role ==='ai'" class="ai-message-stack">
     <div v-for="(bubble, bubbleIndex) in aiBubbles" :key="bubbleIndex" class="chat chat-start items-start ai-message-row">
       <div class="chat-image avatar">
         <div class="w-10 rounded-full">
           <img :src="character.photo" alt=""/>
         </div>
       </div>
       <div class="chat-bubble ai-bubble provenance-trigger whitespace-pre-wrap" role="button" tabindex="0" title="点击查看这条回复的参考依据" @click="openProvenance" @keydown.enter="openProvenance">
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
 <div v-if="showProvenance" class="provenance-overlay" @click.self="showProvenance = false">
   <section class="provenance-panel" role="dialog" aria-modal="true" aria-label="回复依据">
     <div class="provenance-title-row">
       <div><h3>回复依据</h3><p>展示本轮实际提供给模型的聊天原文；不代表模型只依据其中某一句回复。</p></div>
       <button class="btn btn-sm btn-circle btn-ghost" @click="showProvenance = false">✕</button>
     </div>
     <div v-if="retrievedRaw.length" class="provenance-section">
       <h4>本轮检索到的原文</h4>
       <article v-for="(source, index) in retrievedRaw" :key="`${source.source_type}-${index}`" class="provenance-source">
         <div class="provenance-meta">{{ sourceLabel(source.source_type) }}<span v-if="source.timestamp"> · {{ source.timestamp }}</span></div>
         <pre>{{ source.excerpt }}</pre>
       </article>
     </div>
     <div v-else class="provenance-empty">本轮没有额外检索历史原文。</div>
     <div v-if="semanticFacts.length" class="provenance-section">
       <h4>本轮选中的长期记忆</h4>
       <article v-for="fact in semanticFacts" :key="fact.id" class="provenance-source">
         <div class="provenance-meta">{{ fact.subject }} · {{ fact.category }} · memory_id {{ fact.id }}</div>
         <div>{{ fact.fact }}</div>
       </article>
     </div>
     <div v-if="recentOnline.length" class="provenance-section">
       <h4>同时带入的最近 Online Chat（{{ recentOnline.length }} 轮）</h4>
       <article v-for="row in recentOnline" :key="row.message_id" class="provenance-source">
         <div class="provenance-meta">message_id {{ row.message_id }}</div>
         <pre>{{ row.excerpt }}</pre>
       </article>
     </div>
     <div v-if="provenance.has_working_summary" class="provenance-summary">还带入了较早 Online Chat 的工作摘要（覆盖至 message_id {{ provenance.summary_through_message_id || '未知' }}）。</div>
   </section>
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

.ai-message-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ai-message-row { margin: 0; }

.provenance-trigger { cursor: pointer; }

.provenance-overlay { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; padding: 18px; background: rgba(0, 0, 0, .48); }
.provenance-panel { width: min(100%, 480px); max-height: min(78dvh, 640px); overflow: auto; padding: 16px; border: 1px solid rgba(15, 23, 42, .18); border-radius: 18px; background: #ffffff; color: #1f2937; opacity: 1; box-shadow: 0 22px 56px rgba(0, 0, 0, .5); }
.provenance-title-row { display: flex; gap: 12px; align-items: flex-start; justify-content: space-between; }.provenance-title-row h3 { margin: 0; font-weight: 700; }.provenance-title-row p { margin: 4px 0 0; font-size: 12px; opacity: .68; line-height: 1.45; }
.provenance-section { margin-top: 16px; }.provenance-section h4 { margin: 0 0 8px; font-size: 13px; font-weight: 700; }.provenance-source { margin-top: 8px; padding: 10px; border-radius: 10px; background: #f1f5f9; }.provenance-meta { margin-bottom: 5px; color: #64748b; font-size: 11px; }.provenance-source pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 12px; line-height: 1.5; }.provenance-empty,.provenance-summary { margin-top: 14px; padding: 10px; border-radius: 10px; background: #f1f5f9; color: #475569; font-size: 12px; line-height: 1.45; }

.ai-bubble,
.user-bubble {
  min-width: 4rem;
  max-width: 100%;
  word-break: normal;
  overflow-wrap: anywhere;
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
