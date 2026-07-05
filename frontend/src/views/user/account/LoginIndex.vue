<script setup>
import {ref} from "vue";
import {useUserStore} from "@/stores/user.js";
import {useRouter} from "vue-router";
import api from "@/js/http/api.js";
import DynamicBackground from "@/components/background/DynamicBackground.vue";

const username = ref('')
const password = ref('')
const errorMessage = ref('')

const user = useUserStore()
const router = useRouter()

const VIDEO_URL = '/videos/auth-bg.mp4'

async function handleLogin(){
  errorMessage.value=''
  if(!username.value.trim()){
    errorMessage.value='用户名不能为空'
  }else if(!password.value.trim()){
    errorMessage.value='密码不能为空'
  }else{
    try {
      const res = await api.post('/api/user/account/login/',{
        username:username.value,
        password:password.value,
      })
      const data=res.data
      if(data.result ==='success'){
        user.setAccessToken(data.access)
        user.setUserInfo(data)
        await router.push({
          name: 'homepage-index'
        })
      }else{
        errorMessage.value=data.result
      }
    }catch (err){

    }
  }
}
</script>

<template>
  <div class="relative min-h-screen w-full flex items-center justify-center overflow-hidden">
    <DynamicBackground :video-url="VIDEO_URL" :overlay="0.35" />

    <form
      @submit.prevent="handleLogin"
      class="auth-card relative z-10 w-[22rem] max-w-[90vw] rounded-2xl p-8 flex flex-col"
    >
      <h2 class="text-white text-2xl font-semibold mb-6 text-center">登录</h2>

      <label class="text-white/80 text-sm mb-1">用户名</label>
      <input v-model="username" type="text" class="auth-input" placeholder="用户名" />

      <label class="text-white/80 text-sm mb-1 mt-4">密码</label>
      <input v-model="password" type="password" class="auth-input" placeholder="密码" />

      <p v-if="errorMessage" class="text-sm text-red-400 mt-2">{{errorMessage}}</p>

      <button class="auth-btn mt-6">登录</button>

      <div class="flex justify-end mt-2">
        <RouterLink :to="{name:'user-account-register-index'}" class="text-white/60 hover:text-white text-sm transition-colors">
          没有账号?去注册
        </RouterLink>
      </div>
    </form>
  </div>
</template>

<style scoped>
/* 无卡片底:输入框直接浮在视频上 */
.auth-card {
  background: transparent;
  border: none;
  box-shadow: none;
  text-shadow: 0 1px 10px rgba(0, 0, 0, 0.6);
}
/* 左侧渐变暗化:表单区清晰,右侧视频明亮 */
.side-gradient {
  background: linear-gradient(to right,
    rgba(0, 0, 0, 0.65) 0%,
    rgba(0, 0, 0, 0.3) 35%,
    rgba(0, 0, 0, 0) 65%);
}
.auth-input {
  width: 100%;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 0.5rem;
  padding: 0.625rem 0.875rem;
  color: #fff;
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.2s, background 0.2s;
}
.auth-input::placeholder { color: rgba(255, 255, 255, 0.4); }
.auth-input:focus {
  border-color: #2C5C88;
  background: rgba(255, 255, 255, 0.12);
}
.auth-btn {
  width: 100%;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  font-weight: 500;
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 0.5rem;
  padding: 0.7rem;
  font-size: 0.95rem;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  transition: background 0.2s, border-color 0.2s;
}
.auth-btn:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.5);
}
</style>
