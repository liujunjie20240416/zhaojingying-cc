<script setup>

import NavBar from "@/components/navbar/NavBar.vue";
import DynamicBackground from "@/components/background/DynamicBackground.vue";
import {useUserStore} from "@/stores/user.js";
import {useRoute, useRouter} from "vue-router";
import {onMounted} from "vue";
import api from "@/js/http/api.js";

const user = useUserStore()
const route=useRoute()
const router = useRouter()

onMounted(async ()=>{
  try {
    const res = await api.get('/api/user/account/get_user_info/')
    const data = res.data

    if(data.result ==='success'){
      user.setUserInfo(data)
    }
  }catch(err){

  }finally{
    user.setHasPulledUserInfo(true)
    if(route.meta.needLogin && !user.isLogin()){
      await router.replace({
        name:'user-account-login-index'
      })
    }
  }
})
</script>

<template>
<template v-if="route.meta.fullscreen">
  <RouterView/>
</template>
<template v-else>
  <DynamicBackground video-url="/bg-app.mp4" :overlay="0.5" />
  <NavBar>
    <RouterView/>
  </NavBar>
</template>
</template>

<style scoped>

</style>
