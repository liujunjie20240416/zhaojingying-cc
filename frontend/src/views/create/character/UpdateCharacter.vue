<script setup>

import Photo from "@/views/create/character/components/Photo.vue";
import Name from "@/views/create/character/components/Name.vue";
import Profile from "@/views/create/character/components/Profile.vue";
import BackgroundImage from "@/views/create/character/components/BackgroundImage.vue";
import {onMounted, ref, useTemplateRef} from "vue";
import {base64ToFile} from "@/js/utils/base64_to_file.js";
import {useRoute, useRouter} from "vue-router";
import {useUserStore} from "@/stores/user.js";
import api from "@/js/http/api.js";
import Voice from "@/views/create/character/components/Voice.vue";
import WechatImport from "@/views/create/character/components/WechatImport.vue";
import {getApiErrorMessage} from "@/js/http/errors.js";

const user = useUserStore()
const router = useRouter()
const route = useRoute()
const characterId = route.params.character_id
const character = ref(null)
const voices=ref([])
const curVoiceId = ref(null)

async function loadCharacter(){
  try{
    const res = await api.get('/api/create/character/get_single/',{
      params:{
        character_id:characterId,
      }
    })
    const data = res.data
    if(data.result ==='success'){
      character.value = data.character
      voices.value = data.voices
      curVoiceId.value = data.character.voice_id
    }
  }catch(err){
    errorMessage.value = getApiErrorMessage(err, '角色信息加载失败，请稍后重试')
  }
}

onMounted(loadCharacter)

const photoRef = useTemplateRef('photo-ref')
const nameRef = useTemplateRef('name-ref')
const voiceRef = useTemplateRef('voice-ref')
const profileRef = useTemplateRef('profile-ref')
const backgroundImageRef = useTemplateRef('background-image-ref')
const errorMessage = ref('')

async function handleUpdate(){
  const photo = photoRef.value.myPhoto
  const name = nameRef.value.myName?.trim()
  const voice = voiceRef.value.myVoice
  const profile = profileRef.value.myProfile?.trim()
  const backgroundImage = backgroundImageRef.value.myBackgroundImage

  errorMessage.value=''
  if(!photo){
    errorMessage.value='头像不能为空'
  }else if(!name){
    errorMessage.value='名字不能为空'
  }else if(!voice){
    errorMessage.value='音色不能为空'
  }
  else if(!profile){
    errorMessage.value='角色介绍不能为空'
  }else if(!backgroundImage){
    errorMessage.value='聊天背景不能为空'
  }else{
    const formData = new FormData()
    formData.append('character_id',characterId)
    formData.append('name',name)
    formData.append('voice_id',voice)
    formData.append('profile',profile)
    if(photo!==character.value.photo){
      formData.append('photo',base64ToFile(photo,'photo.jpg'))
    }
    if(backgroundImage!==character.value.background_image){
      formData.append('background_image',base64ToFile(backgroundImage,'background_image.jpg'))
    }

    try{
      const res = await api.post('/api/create/character/update/',formData)
      const data = res.data
      if(data.result ==='success'){
        await router.push({
          name:'user-space-index',
          params:{
            user_id:user.id
          }
        })
      }else{
        errorMessage.value=data.result
      }
    }catch(err){
      errorMessage.value = getApiErrorMessage(err, '角色更新失败，请稍后重试')
    }
  }

}

async function handleImported(){
  await loadCharacter()
}

function handleVisibilityChanged(value){
  character.value.imported_memory_visibility = value
}
</script>

<template>
<div v-if="character" class="flex justify-center px-3 sm:px-6">
  <div class="card w-full max-w-120 bg-base-200 shadow-sm mt-6 sm:mt-16">
    <div class="card-body p-4 sm:p-8">
      <h3 class="text-lg font-bold my-4">更新角色</h3>
      <Photo ref="photo-ref" :photo="character.photo"/>
      <Name ref="name-ref" :name="character.name"/>
      <Voice ref="voice-ref" :voices="voices" :curVoiceId="curVoiceId"/>
      <Profile ref="profile-ref" :profile="character.profile"/>
      <BackgroundImage ref="background-image-ref" :backgroundImage="character.background_image"/>

      <WechatImport
        :characterId="character.id"
        :characterName="character.name"
        :memoryVisibility="character.imported_memory_visibility"
        @imported="handleImported"
        @visibilityChanged="handleVisibilityChanged"
      />

      <details v-if="character.style_profile" class="collapse collapse-arrow border border-base-300 bg-base-100">
        <summary class="collapse-title text-sm font-semibold">AI 学习到的说话风格</summary>
        <div class="collapse-content">
          <p class="mb-2 text-xs text-base-content/60">
            此内容只读，会在重新导入聊天记录后自动生成并用于 AI 回复。
          </p>
          <div class="whitespace-pre-wrap break-words rounded-lg bg-base-200 p-3 text-sm leading-6">{{ character.style_profile }}</div>
        </div>
      </details>

      <p v-if="errorMessage" class="text-sm text-red-500">{{errorMessage}}</p>
      <div class="flex justify-center">
        <button @click="handleUpdate" class="btn btn-neutral w-full max-w-60 mt-2">更新</button>
      </div>
    </div>
  </div>
</div>
</template>

<style scoped>

</style>
