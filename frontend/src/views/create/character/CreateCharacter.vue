<script setup>

import Photo from "@/views/create/character/components/Photo.vue";
import Name from "@/views/create/character/components/Name.vue";
import Profile from "@/views/create/character/components/Profile.vue";
import BackgroundImage from "@/views/create/character/components/BackgroundImage.vue";
import {onMounted, ref, useTemplateRef} from "vue";
import {base64ToFile} from "@/js/utils/base64_to_file.js";
import {useRouter} from "vue-router";
import {useUserStore} from "@/stores/user.js";
import api from "@/js/http/api.js";
import Voice from "@/views/create/character/components/Voice.vue";
import WechatImport from "@/views/create/character/components/WechatImport.vue";
import {getApiErrorMessage} from "@/js/http/errors.js";


const user = useUserStore()
const router = useRouter()
const photoRef = useTemplateRef('photo-ref')
const nameRef = useTemplateRef('name-ref')
const voiceRef = useTemplateRef('voice-ref')
const profileRef = useTemplateRef('profile-ref')
const backgroundImageRef = useTemplateRef('background-image-ref')
const errorMessage = ref('')
const voices=ref([])
const curVoiceId = ref(null)
const createdCharacterId = ref(null)
const createdCharacterName = ref('')


onMounted(async () => {
  try {
    const res = await api.get('/api/create/character/voice/get_list/', {})
    const data = res.data
    if (data.result === 'success') {
      voices.value = data.voices
      curVoiceId.value = data.voices[0].id
    }
  } catch (err) {
    console.error(err)
  }
})


async function handleCreate(){
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
    formData.append('name',name)
    formData.append('voice_id',voice)
    formData.append('profile',profile)
    formData.append('photo',base64ToFile(photo,'photo.jpg'))
    formData.append('background_image',base64ToFile(backgroundImage,'background_image.jpg'))

    try{
      const res = await api.post('/api/create/character/create/',formData)
      const data = res.data
      if(data.result ==='success'){
        createdCharacterId.value = data.character_id
        createdCharacterName.value = name
      }else{
        errorMessage.value=data.result
      }
    }catch(err){
      errorMessage.value = getApiErrorMessage(err, '角色创建失败，请稍后重试')
    }
  }

}

async function handleFinish(){
  await router.push({
    name:'user-space-index',
    params:{
      user_id:user.id
    }
  })
}
</script>

<template>
<div class="flex justify-center px-3 sm:px-6">
  <div class="card w-full max-w-120 bg-base-200 shadow-sm mt-6 sm:mt-16">
    <div v-if="!createdCharacterId" class="card-body p-4 sm:p-8">
      <h3 class="text-lg font-bold my-4">创建角色</h3>
      <Photo ref="photo-ref"/>
      <Name ref="name-ref"/>
      <Voice ref="voice-ref" :voices="voices" :curVoiceId="curVoiceId"/>
      <Profile ref="profile-ref"/>
      <BackgroundImage ref="background-image-ref"/>

      <p v-if="errorMessage" class="text-sm text-red-500">{{errorMessage}}</p>
      <div class="flex justify-center">
        <button @click="handleCreate" class="btn btn-neutral w-full max-w-60 mt-2">创建</button>
      </div>
    </div>
    <div v-else class="card-body p-4 sm:p-8">
      <h3 class="text-lg font-bold my-4">导入聊天记录</h3>
      <div class="alert alert-success text-sm p-2">
        <span>角色已创建，可以继续导入聊天记录。</span>
      </div>
      <WechatImport
        :characterId="createdCharacterId"
        :characterName="createdCharacterName"
        memoryVisibility="private"
      />
      <div class="flex justify-center">
        <button @click="handleFinish" class="btn btn-neutral w-full max-w-60 mt-2">完成</button>
      </div>
    </div>
  </div>
</div>
</template>

<style scoped>

</style>
