<script setup>
import SendIcon from "@/components/character/icons/SendIcon.vue";
import MicIcon from "@/components/character/icons/MicIcon.vue";
import {onUnmounted, ref, useTemplateRef} from "vue";
import streamApi from "@/js/http/streamApi.js";
import api from "@/js/http/api.js";
import CONFIG_API from "@/js/config/config.js";
import Microphone from "@/components/character/chat_field/input_field/Microphone.vue";
import {detectUserEmojiContext} from "@/js/utils/emotionEmoji.js";
const props = defineProps(['friendId'])
const emit = defineEmits(['pushBackMessage','addToLastMessage','setLastMessageBubbles','connectionOnline','connectionError','typingFinished'])
const inputRef = useTemplateRef('input-ref')
const message=ref('')
let processId = 0
const showMic = ref(false)
let audioPreparedForNextResponse = false
const imageInputRef = useTemplateRef('image-input-ref')
const pendingImages = ref([])
const uploading = ref(false)
const uploadError = ref('')

function absoluteMediaUrl(url) {
  if (!url || /^https?:\/\//.test(url)) return url
  return `${CONFIG_API.HTTP_URL || ''}${url}`
}
function chooseImages(){ imageInputRef.value?.click() }
function handleImageSelection(event){
  uploadError.value = ''
  const files = Array.from(event.target.files || []).slice(0, Math.max(0, 4-pendingImages.value.length))
  files.forEach(file => {
    if(file.size > 10*1024*1024){ uploadError.value='每张图片不能超过 10MB'; return }
    pendingImages.value.push({file, preview:URL.createObjectURL(file)})
  })
  event.target.value=''
}
function removeImage(index){
  URL.revokeObjectURL(pendingImages.value[index].preview)
  pendingImages.value.splice(index,1)
}
async function uploadImages(){
  const uploaded=[]
  for(const item of pendingImages.value){
    const form=new FormData()
    form.append('friend_id',props.friendId)
    form.append('file',item.file)
    const response=await api.post('/api/friend/message/attachment/upload/',form)
    uploaded.push({...response.data.attachment,url:absoluteMediaUrl(response.data.attachment.url)})
  }
  return uploaded
}


let mediaSource = null;
let sourceBuffer = null;
let audioPlayer = new Audio(); // 全局播放器实例
let audioQueue = [];           // 待写入 Buffer 的二进制队列
let isUpdating = false;        // Buffer 是否正在写入

const tryPlayAudio = () => {
    if (!audioPlayer.paused) return;
    audioPlayer.play().catch(() => {
        // The first call is made from the send gesture. Some browsers keep the
        // promise pending until the first MP3 bytes arrive, so retry there too.
    });
};

const initAudioStream = () => {
    stopAudio();
    audioQueue = [];
    isUpdating = false;
    sourceBuffer = null;

    mediaSource = new MediaSource();
    audioPlayer.src = URL.createObjectURL(mediaSource);

    mediaSource.addEventListener('sourceopen', () => {
        try {
            sourceBuffer = mediaSource.addSourceBuffer('audio/mpeg');
            sourceBuffer.addEventListener('updateend', () => {
                isUpdating = false;
                processQueue();
            });
        } catch (e) {
            console.error("MSE AddSourceBuffer Error:", e);
        }
    });

    // This function is called synchronously from the user's send action, which
    // unlocks autoplay before the asynchronous chat/TTS response arrives.
    tryPlayAudio();
};

const processQueue = () => {
    if (isUpdating || audioQueue.length === 0 || !sourceBuffer || sourceBuffer.updating) {
        return;
    }

    isUpdating = true;
    const chunk = audioQueue.shift();
    try {
        sourceBuffer.appendBuffer(chunk);
    } catch (e) {
        console.error("SourceBuffer Append Error:", e);
        isUpdating = false;
    }
};

const stopAudio = () => {
    audioPlayer.pause();
    audioQueue = [];
    isUpdating = false;

    if (mediaSource) {
        if (mediaSource.readyState === 'open') {
            try {
                mediaSource.endOfStream();
            } catch (e) {
            }
        }
        mediaSource = null;
    }

    if (audioPlayer.src) {
        URL.revokeObjectURL(audioPlayer.src);
        audioPlayer.src = '';
    }
};

const handleAudioChunk = (base64Data) => {  // 将语音片段添加到播放器队列中
    try {
        const binaryString = atob(base64Data);
        const len = binaryString.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }

        audioQueue.push(bytes);
        processQueue();
        tryPlayAudio();
    } catch (e) {
        console.error("Base64 Decode Error:", e);
    }
};

onUnmounted(() => {
    audioPlayer.pause();
    audioPlayer.src = '';
    pendingImages.value.forEach(item => URL.revokeObjectURL(item.preview))
});


async function handleSend(event,audio_msg){
  let content
  if(audio_msg){
    content = audio_msg.trim()
  }else{
    content = message.value.trim()
  }

  if(!content && pendingImages.value.length===0) return
  // Audio playback must be initialised during a user gesture. Initialising at
  // component mount is blocked by current browser autoplay policies.
  if (!audioPreparedForNextResponse) initAudioStream()
  audioPreparedForNextResponse = false
  uploading.value=true
  uploadError.value=''
  let attachments=[]
  try{
    attachments=await uploadImages()
  }catch(error){
    uploadError.value=error.response?.data?.detail || '图片上传失败，请重试'
    uploading.value=false
    return
  }

  const curId = ++ processId
  const createdAt = new Date().toISOString()
  message.value = ''
  pendingImages.value.forEach(item => URL.revokeObjectURL(item.preview))
  pendingImages.value=[]
  uploading.value=false
  emit('connectionOnline')
  emit('pushBackMessage',{role:'user',content:content,attachments,id:crypto.randomUUID(),createdAt})
  emit('pushBackMessage',{role:'ai',content:'',id:crypto.randomUUID(),createdAt,isTyping:true})
    try {
    await streamApi('/api/friend/message/chat/', {
      body: {
        friend_id: props.friendId,
        message: content,
        attachment_ids: attachments.map(item => item.id),
        emotion_context: detectUserEmojiContext(content),
      },
      onmessage(data, isDone) {
        if (curId !== processId) {
          return
        }
        if (isDone) {
          emit('typingFinished')
          return
        }
        if (data.content) {
          emit('addToLastMessage',data.content)
        }
        if (data.bubbles) {
          emit('setLastMessageBubbles', data.bubbles)
        }
        if(data.audio){
          handleAudioChunk(data.audio)
        }
      },
      onerror(err) {
        emit('connectionError')
      },
    })
  } catch (err) {
    emit('connectionError')
  }
}
function focus(){
  inputRef.value.focus()
}
function close() {
  ++ processId
  showMic.value = false
  audioPreparedForNextResponse = false
  stopAudio()
}

function openMicrophone() {
  // Voice input sends after speech recognition, outside the original click.
  // Unlock playback while the microphone button click is still a user gesture.
  initAudioStream()
  audioPreparedForNextResponse = true
  showMic.value = true
}

function closeMicrophone() {
  showMic.value = false
  audioPreparedForNextResponse = false
  stopAudio()
}

function handleStop() {
  ++ processId
  audioPreparedForNextResponse = false
  stopAudio()
}


defineExpose(
    {
      focus,
      close,
    }
)
</script>

<template>
  <form v-if="!showMic" @submit.prevent="handleSend" class="chat-input-form absolute bottom-4 left-2 right-2 flex items-end">
    <div v-if="pendingImages.length" class="image-preview-strip">
      <div v-for="(item,index) in pendingImages" :key="item.preview" class="image-preview-item">
        <img :src="item.preview" alt="待发送图片"><button type="button" @click="removeImage(index)">×</button>
      </div>
    </div>
    <div v-if="uploadError" class="upload-error">{{ uploadError }}</div>
    <input ref="image-input-ref" class="hidden" type="file" accept="image/jpeg,image/png,image/webp" multiple @change="handleImageSelection">
    <input ref="input-ref"
           v-model="message"
        class="input bg-black/30 backdrop-blur-sm text-white text-base w-full h-12 rounded-2xl pr-20 pl-12"
        type="text"
        placeholder="发消息或图片..."
    >
    <button type="submit" :disabled="uploading" class="absolute right-2 bottom-2 w-8 h-8 flex justify-center items-center cursor-pointer disabled:opacity-50">
      <span v-if="uploading" class="loading loading-spinner loading-xs"></span><SendIcon v-else />
    </button>
    <button type="button" @click="openMicrophone" class="absolute right-10 bottom-2 w-8 h-8 flex justify-center items-center cursor-pointer">
      <MicIcon />
    </button>
    <button type="button" @click="chooseImages" class="image-picker-button" title="发送图片">＋</button>
  </form>
  <Microphone v-else
  @close="closeMicrophone"
  @send="handleSend"
  @stop="handleStop"/>

</template>

<style scoped>
.chat-input-form{min-height:3rem}.image-picker-button{position:absolute;left:8px;bottom:8px;width:32px;height:32px;border-radius:999px;color:white;background:rgba(0,0,0,.25);font-size:22px;line-height:1}.image-preview-strip{position:absolute;left:0;right:0;bottom:56px;display:flex;gap:8px;padding:8px;border-radius:14px;background:rgba(0,0,0,.35);backdrop-filter:blur(10px)}.image-preview-item{position:relative;width:58px;height:58px}.image-preview-item img{width:100%;height:100%;object-fit:cover;border-radius:10px}.image-preview-item button{position:absolute;top:-6px;right:-6px;width:20px;height:20px;border-radius:999px;color:white;background:rgba(0,0,0,.75)}.upload-error{position:absolute;left:8px;bottom:55px;color:#fecaca;font-size:12px}
</style>
